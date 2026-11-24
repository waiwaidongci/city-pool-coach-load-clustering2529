from datetime import date, timedelta

from src.domain.spec import DEMO


def demo_rows():
    from src.services.validation import parse_rows
    return parse_rows((DEMO / 'sample.csv').read_text(encoding='utf-8-sig'))


def _jitter(code, salt, span):
    return (sum(ord(char) for char in code) + salt * 11) % span - span // 2


def _shift(row, week, salt):
    hours = min(60.0, max(1.0, row['coaching_hours'] + _jitter(row['coach_id'], salt, 5)))
    peak = min(hours, max(0.0, row['peak_hours'] + _jitter(row['coach_id'], salt + 1, 3)))
    return {'week_start': week.isoformat(), 'coach_id': row['coach_id'], 'pool': row['pool'],
            'coaching_hours': round(hours, 1), 'peak_hours': round(peak, 1),
            'swimmer_visits': max(0, row['swimmer_visits'] + 3 * _jitter(row['coach_id'], salt + 2, 7)),
            'cover_shifts': max(0, row['cover_shifts'] + _jitter(row['coach_id'], salt + 3, 3))}


EXTRA_COACH = {'coach_id': 'CW-038', 'pool': '南堤社区馆', 'coaching_hours': 22.0,
               'peak_hours': 8.0, 'swimmer_visits': 88, 'cover_shifts': 2}

# One-week surge so the first segment shows a real group-to-group migration.
SPIKES = {('CW-031', '2026-11-09'): {'coaching_hours': 30.0, 'peak_hours': 14.0,
                                     'swimmer_visits': 128, 'cover_shifts': 5}}


def demo_weeks():
    """Three deterministic consecutive weeks around the sample week for the migration board."""
    week3 = demo_rows()
    third = date.fromisoformat(week3[0]['week_start'])
    week1, week2 = third - timedelta(days=14), third - timedelta(days=7)
    pools = {week1: {'CW-010': '渡口训练馆', 'CW-020': '南堤社区馆'}, week2: {'CW-010': '渡口训练馆'}}
    skips = {week1: {'CW-035'}, week2: {'CW-036'}}
    weeks = {week1: [], week2: []}
    for row in week3:
        for week, salt in ((week1, 1), (week2, 6)):
            if row['coach_id'] in skips[week]:
                continue
            shifted = _shift(row, week, salt)
            shifted['pool'] = pools[week].get(row['coach_id'], row['pool'])
            shifted.update(SPIKES.get((row['coach_id'], week.isoformat()), {}))
            weeks[week].append(shifted)
    for week, salt in ((week1, 1), (week2, 6)):
        weeks[week].append(_shift(EXTRA_COACH, week, salt))
    return [(week1, 1, weeks[week1]), (week2, 1, weeks[week2]), (third, 1, week3)]
