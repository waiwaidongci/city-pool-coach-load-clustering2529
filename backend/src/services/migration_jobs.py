"""Queue, lease and atomically publish two-segment migration reports."""

import logging
from datetime import timedelta
from sqlalchemy import and_, func, or_, select
from src.config.database import Session
from src.constants.enums import Status
from src.models.migration import BOARD_ID, MigrationBoard, MigrationReport, WeekSnapshot
from src.services.audit import record
from src.services.migration import compute_report
from src.utils.time import now

log = logging.getLogger(__name__)


def latest_revisions(db, weeks):
    stmt = (select(WeekSnapshot.week_start, func.max(WeekSnapshot.revision))
            .where(WeekSnapshot.week_start.in_(weeks)).group_by(WeekSnapshot.week_start))
    return {week: revision for week, revision in db.execute(stmt)}


def enqueue(db, board, actor, reason):
    """Queue a two-segment recompute, collapsing any still-queued job into the newest inputs."""
    revisions = latest_revisions(db, (board.week1, board.week2, board.week3))
    board.generation += 1
    board.updated_at = now()
    job = db.scalar(select(MigrationReport).where(MigrationReport.status == Status.QUEUED)
                    .order_by(MigrationReport.id.desc()).limit(1))
    if job is None:
        job = MigrationReport(author=actor)
        db.add(job)
    job.week1, job.week2, job.week3 = board.week1, board.week2, board.week3
    job.rev1 = revisions.get(board.week1, 0)
    job.rev2 = revisions.get(board.week2, 0)
    job.rev3 = revisions.get(board.week3, 0)
    job.params = dict(board.params)
    job.generation = board.generation
    db.flush()
    record(db, actor, 'migration.queued', job.id, reason=reason,
           revisions=[job.rev1, job.rev2, job.rev3], generation=job.generation)
    return job


def claim():
    stamp = now()
    with Session.begin() as db:
        query = select(MigrationReport).where(or_(MigrationReport.status == Status.QUEUED,
                and_(MigrationReport.status == Status.RUNNING, MigrationReport.lease_until < stamp)))
        job = db.scalar(query.order_by(MigrationReport.id).limit(1).with_for_update(skip_locked=True))
        if job is None:
            return None
        if job.attempt >= 3:
            job.status = Status.FAILED
            job.error = '后台进程多次中断，请发起新的重算'
            job.finished_at = stamp
            record(db, 'worker', 'migration.failed', job.id, reason=job.error)
            return None
        job.status = Status.RUNNING
        job.attempt += 1
        job.lease_until = stamp + timedelta(minutes=3)
        return job.id, job.attempt


def execute(lease):
    job_id, attempt = lease
    with Session() as db:
        job = db.get(MigrationReport, job_id)
        weeks = (job.week1, job.week2, job.week3)
        revisions = (job.rev1, job.rev2, job.rev3)
        params = dict(job.params)
        snapshots = {}
        for week, revision in zip(weeks, revisions):
            snapshot = db.scalar(select(WeekSnapshot).where(WeekSnapshot.week_start == week,
                                                            WeekSnapshot.revision == revision))
            if snapshot:
                snapshots[week] = snapshot.rows
    result = failure = None
    try:
        result = compute_report(weeks, revisions, snapshots, params)
    except ValueError as error:
        failure = str(error)[:500]
    except Exception:
        log.exception('Migration %s failed', job_id)
        failure = '迁移计算失败，请检查周数据口径后重试'
    with Session.begin() as db:
        job = db.get(MigrationReport, job_id, with_for_update=True)
        if job.attempt != attempt or job.status != Status.RUNNING:
            return
        finish(db, job, result, failure)


def finish(db, job, result, failure, actor='worker'):
    job.lease_until = None
    job.finished_at = now()
    job.status = Status.FAILED if failure else Status.COMPLETE
    job.result = None if failure else result
    job.error = failure
    record(db, actor, f'migration.{job.status}', job.id, attempt=job.attempt, reason=failure)
    if not failure:
        publish(db, job, actor)


def publish(db, job, actor='worker'):
    """Switch the board to ``job`` unless a same-or-newer version already superseded it."""
    board = db.get(MigrationBoard, BOARD_ID, with_for_update=True)
    if board is None or (board.week1, board.week2, board.week3) != (job.week1, job.week2, job.week3):
        return False
    stale = (job.generation < board.pub_generation or job.rev1 < board.pub_rev1
             or job.rev2 < board.pub_rev2 or job.rev3 < board.pub_rev3)
    if stale:
        record(db, actor, 'migration.stale', job.id, generation=job.generation)
        return False
    board.current_report_id = job.id
    board.pub_rev1, board.pub_rev2, board.pub_rev3 = job.rev1, job.rev2, job.rev3
    board.pub_generation = job.generation
    board.updated_at = now()
    record(db, actor, 'migration.published', job.id, generation=job.generation)
    return True
