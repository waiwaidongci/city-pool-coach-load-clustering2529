"""Density clustering and supplementary roster decisions for one completed week."""

from array import array
from collections import Counter, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from math import ceil, dist
from statistics import fmean, median, pstdev
from typing import NamedTuple


MAX_COACHES = 5_000
MAX_EXACT_COUNT = 2**53 - 1
FEATURE_LABELS = ("带课时长（小时）", "高峰带课（小时）", "学员签到（人次）", "临时代课（班次）")
ACTIONS = ("进入补班候补", "维持原排", "人工复核", "暂停增排")


class Load(NamedTuple):
    hours: float
    peak: float
    visits: int
    cover: int


@dataclass(frozen=True)
class Coach:
    code: str
    pool: str
    load: Load


@dataclass(frozen=True)
class Point:
    group: int
    kind: str
    neighbors: int
    kth_distance: float | None
    reachable_groups: tuple[int, ...]


def _number(value, label, low, high, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{label}须填写有效数字。")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{label}须填写有效数字。") from None
    if not number.is_finite():
        raise ValueError(f"{label}须填写有限数字。")
    if integer and number != number.to_integral_value():
        raise ValueError(f"{label}须填写整数。")
    if not Decimal(str(low)) <= number <= Decimal(str(high)):
        raise ValueError(f"{label}须在 {low} 至 {high} 之间。")
    return int(number) if integer else float(number)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空。")
    return value.strip()


def _read_window(rows):
    if not rows:
        raise ValueError("台账没有教练记录，请导入一个统计周。")
    if len(rows) > MAX_COACHES:
        raise ValueError(f"单次分析支持最多 {MAX_COACHES} 位教练。")

    week = None
    coaches = {}
    for line, row in enumerate(rows, start=2):
        prefix = f"CSV 第 {line} 行"
        if not isinstance(row, Mapping):
            raise ValueError(f"{prefix}须是教练周负荷记录。")
        raw_week = row.get("week_start")
        try:
            row_week = date.fromisoformat(raw_week) if isinstance(raw_week, str) else None
        except ValueError:
            row_week = None
        if row_week is None or row_week.isoformat() != raw_week or row_week.weekday() != 0:
            raise ValueError(f"{prefix}统计周起始日须为 YYYY-MM-DD 格式的周一。")
        if week is None:
            week = row_week
        if row_week != week:
            raise ValueError(f"{prefix}混入了其他统计周，请分别运行。")
        code = _text(row.get("coach_id"), f"{prefix}教练编号")
        if code in coaches:
            raise ValueError(f"{prefix}教练编号 {code} 重复，请先合并同周的跨馆实绩。")
        pool = _text(row.get("pool"), f"{prefix}归属泳馆")
        hours = _number(row.get("coaching_hours"), f"{prefix}带课时长", 0, 168)
        peak = _number(row.get("peak_hours"), f"{prefix}高峰带课", 0, hours)
        visits = _number(row.get("swimmer_visits"), f"{prefix}学员签到", 0, MAX_EXACT_COUNT, integer=True)
        cover = _number(row.get("cover_shifts"), f"{prefix}临时代课", 0, MAX_EXACT_COUNT, integer=True)
        coaches[code] = Coach(code, pool, Load(hours, peak, visits, cover))
    return week, [coaches[code] for code in sorted(coaches)]


def _standardize(coaches):
    # Subtract an observed value before taking the mean to retain differences
    # between large integer counts that share almost all of their digits.
    statistics = []
    standardized = []
    for column in zip(*(coach.load for coach in coaches)):
        offset = column[0]
        shifted = [value - offset for value in column]
        mean = fmean(shifted)
        sigma = pstdev(shifted)
        statistics.append((offset + mean, sigma))
        standardized.append([(value - mean) / sigma if sigma else 0.0 for value in shifted])
    return list(zip(*standardized)), statistics


def _density(vectors, eps, min_samples):
    neighborhoods = []
    kth_distances = []
    for vector in vectors:
        distances = [dist(vector, other) for other in vectors]
        neighborhoods.append(array("H", (i for i, value in enumerate(distances) if value <= eps)))
        kth_distances.append(sorted(distances)[min_samples - 1] if min_samples <= len(vectors) else None)

    is_core = [len(neighbors) >= min_samples for neighbors in neighborhoods]
    labels = [0] * len(vectors)
    group_count = 0
    # Core points alone connect components. A shared border cannot merge them.
    for origin, core in enumerate(is_core):
        if not core or labels[origin]:
            continue
        group_count += 1
        labels[origin] = group_count
        frontier = deque([origin])
        while frontier:
            point = frontier.popleft()
            for neighbor in neighborhoods[point]:
                if is_core[neighbor] and not labels[neighbor]:
                    labels[neighbor] = group_count
                    frontier.append(neighbor)

    result = []
    for i, neighbors in enumerate(neighborhoods):
        group = labels[i]
        reachable = (group,) if group else ()
        kind = "核心" if group else "噪声"
        if not group:
            core_neighbors = [index for index in neighbors if is_core[index]]
            if core_neighbors:
                nearest = min(core_neighbors, key=lambda j: (dist(vectors[i], vectors[j]), labels[j]))
                group = labels[nearest]
                reachable = tuple(sorted({labels[index] for index in core_neighbors}))
                kind = "边界"
        result.append(Point(group, kind, len(neighbors), kth_distances[i], reachable))
    return result, group_count


def _group_label(group):
    return f"第 {group} 群" if group else "噪声"


def _decision(coach, point, typical_hours, hold_hours, cover_hours, has_variation):
    hours = Decimal(str(coach.load.hours))
    hold = Decimal(str(hold_hours))
    cover = Decimal(str(cover_hours))
    if hours >= hold:
        return ACTIONS[3], f"个人达到 {hold_hours:g} 小时暂停线"
    if point.kind == "噪声":
        return ACTIONS[2], "邻域内无核心教练，核对课表、签到和代课单"
    if len(point.reachable_groups) > 1:
        return ACTIONS[2], "同时贴近多个群组，核对教练实际排课"
    if point.kind == "边界":
        return ACTIONS[2], "邻居数低于核心门槛，核对教练实际排课"
    if not has_variation:
        return ACTIONS[2], "四项指标相同，先核对台账口径"
    if hours + cover >= hold:
        return ACTIONS[1], f"个人补班后将达到 {hold_hours:g} 小时暂停线"
    if typical_hours + cover >= hold:
        return ACTIONS[1], f"本群中位数加补班后将达到 {hold_hours:g} 小时暂停线"
    return ACTIONS[0], f"个人与本群中位数加补班后均低于 {hold_hours:g} 小时"


def _table(title, columns, rows):
    return {"title": title, "columns": columns, "rows": rows}


def analyze(rows, params):
    """Return a JSON-safe report for the imported batch and proposed cover shift."""
    eps = _number(params.get("eps", 0.65), "邻域半径 ε", 0.05, 8)
    min_samples = _number(params.get("min_samples", 5), "核心点最少邻居数", 2, 500, integer=True)
    hold_hours = _number(params.get("hold_hours", 28), "暂停增排线", 1, 168)
    cover_hours = _number(params.get("cover_hours", 6), "拟补班时长", 0.5, 168)
    week, coaches = _read_window(rows)
    vectors, statistics = _standardize(coaches)
    points, group_count = _density(vectors, eps, min_samples)

    group_loads = [[] for _ in range(group_count + 1)]
    for coach, point in zip(coaches, points):
        group_loads[point.group].append(coach.load)
    profiles = {
        group: [median(Decimal(str(value)) for value in feature) for feature in zip(*loads)]
        for group, loads in enumerate(group_loads) if loads
    }
    scale_rows = [
        [label, round(mean, 6), round(sigma, 6), "参与距离" if sigma else "常量列，标准分为 0"]
        for label, (mean, sigma) in zip(FEATURE_LABELS, statistics)
    ]
    has_variation = any(sigma for _, sigma in statistics)
    types_by_group = Counter((point.group, point.kind) for point in points)
    actions_by_group = Counter()
    actions = Counter()
    detail_rows = []
    for coach, point in zip(coaches, points):
        typical_hours = profiles[point.group][0] if point.group else None
        action, reason = _decision(coach, point, typical_hours, hold_hours, cover_hours, has_variation)
        actions[action] += 1
        actions_by_group[point.group, action] += 1
        detail_rows.append([
            coach.code, coach.pool, *coach.load, _group_label(point.group), point.kind,
            point.neighbors, round(point.kth_distance, 6) if point.kth_distance is not None else "—",
            "、".join(map(_group_label, point.reachable_groups)) or "—", action, reason,
        ])

    group_rows = [
        [
            _group_label(group), len(group_loads[group]), types_by_group[group, "核心"],
            types_by_group[group, "边界"], *[float(value) for value in profiles[group]],
            *[actions_by_group[group, action] for action in ACTIONS],
        ]
        for group in range(1, group_count + 1)
    ]
    noise = len(group_loads[0])
    core_count = sum(point.kind == "核心" for point in points)
    border_count = len(coaches) - core_count - noise
    multi_group_borders = sum(len(point.reachable_groups) > 1 for point in points)
    kth_values = sorted(point.kth_distance for point in points if point.kth_distance is not None)
    kth_rows = [[eps, min_samples, "—", "—", "—", "—"]]
    if kth_values:
        kth_rows = [[eps, min_samples, *[round(value, 6) for value in (
            kth_values[0], median(kth_values), kth_values[ceil(0.90 * len(kth_values)) - 1], kth_values[-1],
        )]]]

    notes = [
        "用全市同一周的数据计算均值和总体标准差。距离为四项标准分的欧氏距离，邻域包括距离等于 ε 的记录和教练自身。",
        "第 k 近距离中 k 等于最少邻居数，包含自身距离 0。90 分位取排序后向上取整所得的记录位次，距离和标准化统计最多显示六位小数。",
        f"{multi_group_borders} 位边界教练同时贴近多个群。归入最近核心教练的群，等距取较小群号。群号按核心教练编号确定，每批重新计算。",
        "个人达到暂停线优先暂停增排。边界及噪声先复核。核心教练本人及所属群带课时长中位数加拟补班时长后都低于暂停线时，才进入补班候补。",
        "发布后由值班主管核对教练资质、实际可用时段及下一周课表。",
    ]
    if not has_variation:
        notes.append("四项负荷完全相同，本批低于暂停线的教练均交人工复核。")
    if min_samples > len(coaches):
        notes.append("最少邻居数高于台账人数，无法形成核心点，第 k 近距离留空。")
    elif not group_count:
        notes.append("当前半径与邻居门槛下未形成密集群。结合第 k 近距离及原台账复核。")

    return {
        "summary": (
            f"{week.isoformat()} 起始周共 {len(coaches)} 位教练，得到 {group_count} 个群和 {noise} 位噪声。"
            f"拟补班 {cover_hours:g} 小时，建议 {actions[ACTIONS[0]]} 位进入候补，"
            f"{actions[ACTIONS[1]]} 位维持原排，{actions[ACTIONS[2]]} 位人工复核，"
            f"{actions[ACTIONS[3]]} 位暂停增排。"
        ),
        "metrics": [
            {"label": "密度群组", "value": group_count, "detail": f"{core_count} 位核心 · {border_count} 位边界"},
            {"label": "噪声教练", "value": noise, "detail": f"占台账 {noise / len(coaches):.1%} · 邻域无核心点"},
            {"label": "补班候补", "value": actions[ACTIONS[0]], "detail": f"拟补班 {cover_hours:g} 小时"},
            {"label": "暂停增排", "value": actions[ACTIONS[3]], "detail": f"个人带课达到 {hold_hours:g} 小时"},
        ],
        "tables": [
            _table("群组负荷与补班去向", [
                "群组", "教练人数", "核心人数", "边界人数", "带课中位（小时）", "高峰中位（小时）",
                "签到中位（人次）", "代课中位（班次）", *ACTIONS,
            ], group_rows),
            _table("逐位教练诊断与补班建议", [
                "教练编号", "归属泳馆", *FEATURE_LABELS, "群组", "点类型", "邻居数（含自身）",
                "第 k 近距离", "可达核心群", "排班建议", "判断依据",
            ], detail_rows),
            _table("邻域半径诊断", ["ε", "最少邻居数 k", "最小第 k 近距离", "中位第 k 近距离", "90 分位第 k 近距离", "最大第 k 近距离"], kth_rows),
            _table("标准化输入诊断", ["指标", "原始均值", "总体标准差", "计算口径"], scale_rows),
        ],
        "notes": notes,
        "series": [{"label": action, "value": actions[action]} for action in ACTIONS],
    }
