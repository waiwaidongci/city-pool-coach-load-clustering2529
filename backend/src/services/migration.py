"""Three-week load migration: judge next-week coaches against the prior week's scale and core clusters."""

from collections import Counter
from math import dist
from statistics import median

from src.services.analysis import FEATURE_LABELS, _density, _number, _read_window, _standardize

CATEGORIES = ("留群", "转群", "入群", "出群", "持续噪声")
SEGMENT_KEYS = ("from_week", "to_week", "from_revision", "to_revision")


def validate_week(rows):
    """Check one imported week revision and return its week and parsed coaches."""
    return _read_window(rows)


def normalize_params(params):
    return {
        "eps": _number(params.get("eps", 0.65), "邻域半径 ε", 0.05, 8),
        "min_samples": _number(params.get("min_samples", 5), "核心点最少邻居数", 2, 500, integer=True),
    }


def _medians(loads):
    return [float(median(feature)) for feature in zip(*loads)]


def compute_segment(from_week, from_revision, coaches_from, to_week, to_revision, coaches_to, eps, min_samples):
    """Classify each ``to_week`` coach with ``from_week``'s standardization scale and core clusters."""
    vectors, statistics = _standardize(coaches_from)
    points, group_count = _density(vectors, eps, min_samples)
    core = tuple(point.kind == "核心" for point in points)

    def destination(load):
        z_scores = tuple((value - mean) / sigma if sigma else 0.0
                         for value, (mean, sigma) in zip(load, statistics))
        neighbors = 0
        core_hits = []
        for vector, is_core, point in zip(vectors, core, points):
            distance = dist(z_scores, vector)
            if distance <= eps:
                neighbors += 1
                if is_core:
                    core_hits.append((distance, point.group))
        if core_hits:
            nearest, group = min(core_hits)
            return group, neighbors, len(core_hits), round(nearest, 6)
        return 0, neighbors, 0, None

    former = {coach.code: (coach, point) for coach, point in zip(coaches_from, points)}
    current = {coach.code: coach for coach in coaches_to}

    continuing = []
    categories = Counter()
    transfers = 0
    for code in sorted(former.keys() & current.keys()):
        coach_from, point = former[code]
        coach_to = current[code]
        dest_group, neighbors, core_neighbors, nearest = destination(coach_to.load)
        origin_group = point.group
        if origin_group and dest_group == origin_group:
            category = "留群"
        elif origin_group and dest_group:
            category = "转群"
        elif origin_group:
            category = "出群"
        elif dest_group:
            category = "入群"
        else:
            category = "持续噪声"
        transfer = coach_from.pool != coach_to.pool
        categories[category] += 1
        transfers += transfer
        continuing.append({
            "code": code, "pool_from": coach_from.pool, "pool_to": coach_to.pool, "transfer": transfer,
            "origin_group": origin_group, "origin_kind": point.kind,
            "dest_group": dest_group, "dest_neighbors": neighbors,
            "dest_core_neighbors": core_neighbors, "nearest": nearest, "category": category,
        })

    newcomers = []
    for code in sorted(current.keys() - former.keys()):
        coach = current[code]
        dest_group, neighbors, core_neighbors, nearest = destination(coach.load)
        newcomers.append({
            "code": code, "pool": coach.pool, "dest_group": dest_group,
            "dest_neighbors": neighbors, "dest_core_neighbors": core_neighbors, "nearest": nearest,
        })

    absent = [
        {"code": code, "pool": former[code][0].pool,
         "origin_group": former[code][1].group, "origin_kind": former[code][1].kind}
        for code in sorted(former.keys() - current.keys())
    ]

    group_loads = [[] for _ in range(group_count + 1)]
    for coach, point in zip(coaches_from, points):
        group_loads[point.group].append(coach.load)
    types_by_group = Counter((point.group, point.kind) for point in points)
    inflow, outflow = Counter(), Counter()
    for entry in continuing:
        if entry["dest_group"]:
            inflow[entry["dest_group"]] += 1
        if entry["origin_group"] and entry["origin_group"] != entry["dest_group"]:
            outflow[entry["origin_group"]] += 1
    for entry in newcomers:
        if entry["dest_group"]:
            inflow[entry["dest_group"]] += 1
    for entry in absent:
        if entry["origin_group"]:
            outflow[entry["origin_group"]] += 1
    groups = []
    for group in range(1, group_count + 1):
        hours, peak, visits, cover = _medians(group_loads[group])
        groups.append({
            "group": group, "size": len(group_loads[group]),
            "core": types_by_group[group, "核心"], "border": types_by_group[group, "边界"],
            "median_hours": hours, "median_peak": peak, "median_visits": visits, "median_cover": cover,
            "inflow": inflow[group], "outflow": outflow[group],
        })

    return {
        "from_week": from_week.isoformat(), "to_week": to_week.isoformat(),
        "from_revision": from_revision, "to_revision": to_revision,
        "scale": [
            {"label": label, "mean": round(mean, 6), "sigma": round(sigma, 6)}
            for label, (mean, sigma) in zip(FEATURE_LABELS, statistics)
        ],
        "groups": groups,
        "summary": {
            "continuing": len(continuing), "stayed": categories["留群"], "moved": categories["转群"],
            "entered": categories["入群"], "left": categories["出群"], "still_noise": categories["持续噪声"],
            "transfers": transfers, "newcomers": len(newcomers), "absent": len(absent),
        },
        "coaches": continuing, "newcomers": newcomers, "absent": absent,
    }


def compute_report(weeks, revisions, snapshots, params):
    """Compute both migration segments; any failing segment fails the whole report."""
    options = normalize_params(params)
    coaches = {}
    for week in weeks:
        rows = snapshots.get(week)
        if rows is None:
            raise ValueError(f"{week.isoformat()} 起始周还没有周数据修订，请先导入该周数据。")
        found, parsed = validate_week(rows)
        if found != week:
            raise ValueError(f"{week.isoformat()} 起始周的修订数据实际属于 {found.isoformat()}，请重新导入。")
        coaches[week] = parsed
    eps, min_samples = options["eps"], options["min_samples"]
    segments = [
        compute_segment(weeks[0], revisions[0], coaches[weeks[0]],
                        weeks[1], revisions[1], coaches[weeks[1]], eps, min_samples),
        compute_segment(weeks[1], revisions[1], coaches[weeks[1]],
                        weeks[2], revisions[2], coaches[weeks[2]], eps, min_samples),
    ]
    first, second = (segment["summary"] for segment in segments)
    headline = (
        f"{weeks[0].isoformat()} 至 {weeks[2].isoformat()} 连续三周负荷迁移（各周修订 "
        f"{' / '.join(map(str, revisions))}）：第一段留群 {first['stayed']} · 转群 {first['moved']}，"
        f"第二段留群 {second['stayed']} · 转群 {second['moved']}；新进 "
        f"{first['newcomers'] + second['newcomers']} 位、缺席 {first['absent'] + second['absent']} 位、"
        f"转馆 {first['transfers'] + second['transfers']} 位。"
    )
    return {
        "headline": headline,
        "weeks": [week.isoformat() for week in weeks],
        "revisions": list(revisions),
        "params": options,
        "segments": segments,
        "notes": [
            "每段以前一周全市均值与总体标准差为尺度标准化后一周实绩，再按前一周核心群判断去向：ε 邻域内取最近核心教练的群，等距取较小群号，够不到任何核心教练记为噪声。",
            "转馆按同一教练编号比较前后两周归属泳馆。新进与缺席教练单列，不计入留群与转群。",
            "周数据更正按各周修订号取最新；两段算齐后一次性切换，任一段失败时看板继续显示上一可用版本。",
        ],
    }


def suggestion_basis(report_id, generation, result, code):
    """Snapshot the migration facts a substitute-shift suggestion is issued against."""
    segment = result["segments"][1]
    entry = next((item for item in segment["coaches"] if item["code"] == code), None)
    if entry is None:
        entry = next((item for item in segment["newcomers"] if item["code"] == code), None)
    if entry is None:
        return None
    return {
        "report_id": report_id,
        "generation": generation,
        "weeks": result["weeks"],
        "revisions": result["revisions"],
        "segment": {key: segment[key] for key in SEGMENT_KEYS},
        "coach": entry,
        "params": result["params"],
    }
