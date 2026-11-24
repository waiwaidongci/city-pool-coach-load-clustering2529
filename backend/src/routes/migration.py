from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role, Status
from src.controllers.auth import allow, current
from src.models.identity import User
from src.models.migration import BOARD_ID, MigrationBoard, MigrationReport, ShiftSuggestion, WeekSnapshot
from src.schemas.requests import BoardSetup, SuggestionRequest, WeekImport
from src.services.analysis import ACTIONS
from src.services.audit import record
from src.services.datasets import checksum
from src.services.migration import normalize_params, suggestion_basis, validate_week
from src.services.migration_jobs import enqueue, latest_revisions
from src.services.validation import parse_rows
from src.utils import views
from src.utils.time import iso
from src.utils.wakeup import wake

router = APIRouter(prefix='/migration')


def recent_snapshots(db):
    return [views.week_snapshot(item) for item in
            db.scalars(select(WeekSnapshot).order_by(WeekSnapshot.id.desc()).limit(8))]


def board_view(db, board):
    current_report = db.get(MigrationReport, board.current_report_id) if board.current_report_id else None
    pending = db.scalar(select(MigrationReport)
                        .where(MigrationReport.status.in_((Status.QUEUED, Status.RUNNING)))
                        .order_by(MigrationReport.id.desc()).limit(1))
    failed = db.scalar(select(MigrationReport)
                       .where(MigrationReport.status == Status.FAILED,
                              MigrationReport.generation > board.pub_generation)
                       .order_by(MigrationReport.id.desc()).limit(1))
    revisions = latest_revisions(db, (board.week1, board.week2, board.week3))
    weeks = [board.week1, board.week2, board.week3]
    return {
        'board': {'weeks': [week.isoformat() for week in weeks], 'params': board.params,
                  'generation': board.generation, 'updated_at': iso(board.updated_at)},
        'current_report': views.migration_report(current_report, detail=True) if current_report else None,
        'pending': views.migration_report(pending) if pending else None,
        'last_failed': views.migration_report(failed) if failed else None,
        'latest_revisions': {week.isoformat(): revisions.get(week) for week in weeks},
        'recent_snapshots': recent_snapshots(db),
    }


@router.get('/board')
def board(db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(MigrationBoard, BOARD_ID)
    if not item:
        return {'board': None, 'current_report': None, 'pending': None, 'last_failed': None,
                'latest_revisions': {}, 'recent_snapshots': recent_snapshots(db)}
    return board_view(db, item)


@router.post('/board', status_code=201)
def setup(body: BoardSetup, db: Session = Depends(connection),
          actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    if body.week_start.weekday() != 0:
        raise ValueError('连续三周的第一周须从周一开始')
    weeks = [body.week_start, body.week_start + timedelta(days=7), body.week_start + timedelta(days=14)]
    params = normalize_params(body.model_dump(include={'eps', 'min_samples'}))
    board = db.get(MigrationBoard, BOARD_ID, with_for_update=True)
    if board is None:
        board = MigrationBoard(id=BOARD_ID, generation=0, pub_generation=0)
        db.add(board)
    board.week1, board.week2, board.week3 = weeks
    board.params = params
    board.current_report_id = None
    board.pub_rev1 = board.pub_rev2 = board.pub_rev3 = 0
    board.pub_generation = board.generation
    enqueue(db, board, actor.username, '设置连续三周看板')
    record(db, actor.username, 'migration.board_setup', BOARD_ID,
           weeks=[week.isoformat() for week in weeks])
    db.commit()
    wake()
    return board_view(db, board)


@router.post('/recompute', status_code=202)
def recompute(db: Session = Depends(connection), actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    board = db.get(MigrationBoard, BOARD_ID, with_for_update=True)
    if not board:
        raise HTTPException(404, '尚未设置连续三周看板')
    job = enqueue(db, board, actor.username, '手动重算')
    db.commit()
    wake()
    return views.migration_report(job)


@router.post('/weeks', status_code=201)
def import_week(body: WeekImport, db: Session = Depends(connection),
                actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    if body.week_start.weekday() != 0:
        raise ValueError('统计周起始日须为周一')
    rows = parse_rows(body.csv_text)
    week, _coaches = validate_week(rows)
    if week != body.week_start:
        raise ValueError(f'CSV 统计周 {week.isoformat()} 与所选周 {body.week_start.isoformat()} 不一致')
    signature = checksum(rows)
    latest = latest_revisions(db, (body.week_start,)).get(body.week_start, 0)
    existing = db.scalar(select(WeekSnapshot).where(WeekSnapshot.week_start == body.week_start,
                                                    WeekSnapshot.revision == body.revision))
    if existing:
        if existing.checksum != signature:
            raise HTTPException(409, '该周此修订号已存在不同内容，请使用更高的修订号')
        return {**views.week_snapshot(existing), 'created': False,
                'current': existing.revision == latest, 'latest_revision': latest}
    snapshot = WeekSnapshot(week_start=body.week_start, revision=body.revision, rows=rows,
                            row_count=len(rows), checksum=signature, author=actor.username)
    try:
        with db.begin_nested():
            db.add(snapshot)
            db.flush()
    except IntegrityError:
        raise HTTPException(409, '该周此修订号已存在，请刷新后核对最新修订号') from None
    is_current = body.revision > latest
    board = db.get(MigrationBoard, BOARD_ID)
    enqueued = False
    if is_current and board and body.week_start in (board.week1, board.week2, board.week3):
        enqueue(db, board, actor.username, f'{body.week_start.isoformat()} 第 {body.revision} 修订')
        enqueued = True
    record(db, actor.username, 'migration.week_imported', snapshot.id,
           week=body.week_start.isoformat(), revision=body.revision, current=is_current)
    db.commit()
    if enqueued:
        wake()
    return {**views.week_snapshot(snapshot), 'created': True, 'current': is_current,
            'latest_revision': max(latest, body.revision)}


@router.get('/reports')
def reports(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.migration_report(item) for item in
            db.scalars(select(MigrationReport).order_by(MigrationReport.id.desc()).limit(50))]


@router.get('/reports/{report_id}')
def report(report_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(MigrationReport, report_id)
    if not item:
        raise HTTPException(404, '找不到该迁移版本')
    return views.migration_report(item, detail=True)


@router.get('/suggestions')
def suggestions(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.suggestion(item) for item in
            db.scalars(select(ShiftSuggestion).order_by(ShiftSuggestion.id.desc()).limit(100))]


@router.post('/suggestions', status_code=201)
def suggest(body: SuggestionRequest, db: Session = Depends(connection),
            actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    board = db.get(MigrationBoard, BOARD_ID)
    report = db.get(MigrationReport, board.current_report_id) if board and board.current_report_id else None
    if not report or not report.result:
        raise HTTPException(409, '迁移看板还没有已发布的版本，暂不能发出补班建议')
    if body.action not in ACTIONS:
        raise ValueError('请选择项目支持的补班建议')
    basis = suggestion_basis(report.id, report.generation, report.result, body.coach_id)
    if basis is None:
        raise ValueError(f'教练 {body.coach_id} 不在当前版本第二段迁移中，无法据此发出建议')
    item = ShiftSuggestion(coach_id=body.coach_id, target_week=date.fromisoformat(report.result['weeks'][2]),
                           action=body.action, note=body.note, report_id=report.id, basis=basis,
                           author=actor.username)
    db.add(item)
    db.flush()
    record(db, actor.username, 'migration.suggestion_issued', item.id,
           coach=body.coach_id, action=body.action, report_id=report.id)
    db.commit()
    return views.suggestion(item)
