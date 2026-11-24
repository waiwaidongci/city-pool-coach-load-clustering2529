from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def week_snapshot(item):
    return {'id': item.id, 'week_start': item.week_start.isoformat(), 'revision': item.revision,
            'row_count': item.row_count, 'checksum': item.checksum, 'author': item.author,
            'created_at': iso(item.created_at)}


def migration_report(item, detail=False):
    view = {'id': item.id, 'weeks': [item.week1.isoformat(), item.week2.isoformat(), item.week3.isoformat()],
            'revisions': [item.rev1, item.rev2, item.rev3], 'params': item.params,
            'generation': item.generation, 'status': item.status, 'error': item.error,
            'author': item.author, 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at)}
    if detail:
        view['result'] = item.result
    return view


def suggestion(item):
    return {'id': item.id, 'coach_id': item.coach_id, 'target_week': item.target_week.isoformat(),
            'action': item.action, 'note': item.note, 'author': item.author, 'report_id': item.report_id,
            'basis': item.basis, 'created_at': iso(item.created_at)}
