import json
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Role, Status
from src.domain.demo import demo_rows, demo_weeks
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.migration import BOARD_ID, MigrationBoard, MigrationReport, ShiftSuggestion, WeekSnapshot
from src.models.source import Dataset
from src.services.datasets import checksum, save
from src.services.migration import compute_report, suggestion_basis
from src.services.migration_jobs import finish
from src.utils.security import hash_password


def seed_migration(db):
    weeks = demo_weeks()
    for week, revision, rows in weeks:
        db.add(WeekSnapshot(week_start=week, revision=revision, rows=rows, row_count=len(rows),
                            checksum=checksum(rows), author='seed'))
    starts = [week for week, _revision, _rows in weeks]
    board = MigrationBoard(id=BOARD_ID, week1=starts[0], week2=starts[1], week3=starts[2],
                           params={'eps': 0.65, 'min_samples': 5}, generation=1)
    db.add(board)
    snapshots = {week: rows for week, _revision, rows in weeks}
    result = compute_report(starts, [1, 1, 1], snapshots, board.params)
    job = MigrationReport(week1=starts[0], week2=starts[1], week3=starts[2], rev1=1, rev2=1, rev3=1,
                          params=dict(board.params), generation=1, status=Status.RUNNING,
                          attempt=1, author='seed')
    db.add(job)
    db.flush()
    finish(db, job, result, None, actor='seed')
    segment = result['segments'][1]
    picks = []
    transfer = next((entry for entry in segment['coaches'] if entry['transfer']), None)
    if transfer:
        picks.append((transfer['code'], '人工复核', '转馆教练，请新归属馆值班主管核对课表、签到和实际可用时段。'))
    moved = next((entry for entry in segment['coaches'] if entry['category'] == '转群'), None)
    if moved:
        picks.append((moved['code'], '人工复核', '迁入新群，请值班主管核对课表、签到和代课单。'))
    if segment['newcomers']:
        picks.append((segment['newcomers'][0]['code'], '人工复核', '新进教练，核对资质、实际可用时段及下一周课表。'))
    for code, action, note in picks:
        db.add(ShiftSuggestion(coach_id=code, target_week=starts[2], action=action, note=note,
                               report_id=job.id,
                               basis=suggestion_basis(job.id, job.generation, result, code),
                               author='seed'))
    return {'weeks': [week.isoformat() for week in starts], 'report_id': job.id,
            'suggestions': len(picks)}


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        summary = {'users': db.scalar(select(func.count()).select_from(User)),
                   'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                   'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                   'created_dataset': created}
        if not db.get(MigrationBoard, BOARD_ID):
            summary['migration'] = seed_migration(db)
        return summary


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
