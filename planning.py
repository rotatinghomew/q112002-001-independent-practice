"""下次交叉复评批次规划。

规划只是补证承诺：备发（planned）安排在真实评分到达前不参与尺度计算，
不能提前当成评分可比。

缺口两类（与 scale 模块的边状态对应）：
- coverage_gap：有效共同评阅不足，补评可以恢复尺度联系；
- conflict：配对极差超门槛，需要核实评委分歧。

约束：
- 只能选该老师还没读过的锚作答；不能复评自己首次评过的同份作答；
  外班老师只评外班锚（from_room 与本人班级不同），且 goal 须在授权名单内。
- 容量按 (锚作答, 评委) 计分钟：同一份评阅即使同时补两个班的联系也只算一次。
- 已接单（accepted）工作一律保留并占用容量；备发（planned）可被重算移除。
- 优先级：让更多仍待确认学生获得公平判断的缺口优先，而不是按全校缺评分份数。
"""

from dataclasses import dataclass

from observation import take_snapshot
from scale import (
    link_statuses,
    active_bridge_marks,
    CONFLICT,
    COVERAGE_GAP,
    LINKED,
)
from dispositions import reconcile


ACCEPTED = "accepted"
PLANNED = "planned"


@dataclass(frozen=True)
class Gap:
    goal: str
    left_room: str
    right_room: str
    kind: str                 # coverage_gap / conflict
    pair_count: int
    affected_students: tuple[str, ...]
    detail: str

    @property
    def key(self):
        return (self.goal, tuple(sorted((self.left_room, self.right_room))))


@dataclass(frozen=True)
class Candidate:
    anchor: str
    goal: str
    rater: str
    minutes: int
    closes_gaps: tuple[Gap, ...]
    weight: int               # 覆盖到的仍待确认学生数（去重）
    needs_counterpart: bool   # 锚上还缺对侧班评分，本评阅需等配对才生效
    reason: str


@dataclass(frozen=True)
class RemovedPlan:
    anchor: str
    goal: str
    rater: str
    reason: str
    follow_up: str


@dataclass(frozen=True)
class BatchPlan:
    gaps: tuple[Gap, ...]
    candidates: tuple[Candidate, ...]
    accepted: tuple           # 已接单，原样保留
    retained_planned: tuple   # 仍合适的备发
    removed_planned: tuple[RemovedPlan, ...]
    proposed: tuple[Candidate, ...]   # 拟发新批次（容量内贪心选择）
    capacity_left: dict
    covered_by_assignment: tuple[Gap, ...]   # 接单/备发已承诺，等评分落地
    unfillable: tuple[Gap, ...]       # 无合格候选、需不同班实际评阅的缺口


def _pending_student_goals(tables, objectives, current_minute):
    work = reconcile(tables, objectives, current_minute=current_minute)
    pending = set()
    for receipt in work.open_receipts:
        pending.add((receipt["student"], receipt["goal"]))
    for evaluation in work.new_slips:
        pending.add((evaluation.student, evaluation.goal))
    for correction in work.corrections:
        pending.add((correction.student, correction.goal))
    return pending, work


def _student_rooms(snapshot):
    rooms = {}
    for row in snapshot.rows("attempts"):
        rooms.setdefault((row["student"], row["goal"]), set()).add(row["room"])
    return rooms


def find_gaps(tables, objectives, current_minute):
    """列出全部未连通的尺度边，以及每条边卡住的仍待确认学生。"""
    snapshot = take_snapshot(tables, as_of=current_minute)
    pending, _ = _pending_student_goals(tables, objectives, current_minute)
    rooms_by_student = _student_rooms(snapshot)
    gaps = []
    for status in link_statuses(snapshot, objectives):
        if status.status == LINKED:
            continue
        edge_rooms = set(status.key)
        affected = tuple(sorted(
            student for (student, goal) in pending
            if goal == status.goal and edge_rooms & rooms_by_student.get((student, goal), set())
        ))
        gaps.append(Gap(
            goal=status.goal,
            left_room=status.left_room,
            right_room=status.right_room,
            kind=status.status,
            pair_count=status.pair_count,
            affected_students=affected,
            detail=status.break_reason,
        ))
    return tuple(gaps)


def _anchor_map(snapshot):
    return {row["anchor"]: row for row in snapshot.rows("anchors")}


def _read_raters(snapshot):
    """每位评委已读过（有效评分登记在案）的锚作答集合。"""
    read = {}
    for mark in active_bridge_marks(snapshot):
        read.setdefault(mark["rater"], set()).add(mark["attempt"])
    return read


def _rounds(snapshot, current_minute):
    rounds = {}
    for row in snapshot.rows("review_rounds"):
        if row["minute"] <= current_minute:
            rounds[row["rater"]] = row
    return rounds


def find_candidates(tables, objectives, current_minute):
    """枚举所有合规 (锚, 评委) 候选及其可补的缺口。"""
    snapshot = take_snapshot(tables, as_of=current_minute)
    gaps = find_gaps(tables, objectives, current_minute)
    anchors = _anchor_map(snapshot)
    read = _read_raters(snapshot)
    rounds = _rounds(snapshot, current_minute)
    active = active_bridge_marks(snapshot)
    rooms_with_mark = {}
    for mark in active:
        rooms_with_mark.setdefault(mark["attempt"], set()).add(mark["room"])

    candidates = []
    for anchor_id, anchor in anchors.items():
        if anchor["ready"] > current_minute:
            continue
        goal = anchor["goal"]
        anchor_rooms = rooms_with_mark.get(anchor_id, set())
        for rater, round_row in rounds.items():
            if goal not in round_row["goals"]:
                continue
            if rater == anchor["first_rater"]:
                continue
            if anchor_id in read.get(rater, set()):
                continue
            if round_row["room"] == anchor["from_room"]:
                continue  # 外班复评：不评本班送出的锚
            rater_room = round_row["room"]
            closes = []
            for gap in gaps:
                if gap.goal != goal:
                    continue
                edge = {gap.left_room, gap.right_room}
                if gap.kind == CONFLICT:
                    # 分歧核实：锚上须已有班对两侧评分，由中立评委复读仲裁
                    if edge <= anchor_rooms:
                        closes.append(gap)
                else:
                    # 覆盖补证：评委来自班对之一，其评分与对侧评分构成配对
                    if rater_room in edge:
                        closes.append(gap)
            closes = tuple(closes)
            if not closes:
                continue
            students = set()
            for gap in closes:
                students.update(gap.affected_students)
            # 覆盖缺口下，锚上已有对侧班评分则到达即成配对；分歧核实不存在等待
            needs_counterpart = any(
                gap.kind == COVERAGE_GAP
                and not (anchor_rooms & ({gap.left_room, gap.right_room} - {rater_room}))
                for gap in closes
            )
            candidates.append(Candidate(
                anchor=anchor_id,
                goal=goal,
                rater=rater,
                minutes=anchor["minutes"],
                closes_gaps=closes,
                weight=len(students),
                needs_counterpart=needs_counterpart,
                reason=(
                    f"补 {', '.join(g.left_room + '-' + g.right_room for g in closes)}；"
                    + ("需对侧班评分到达才形成配对；" if needs_counterpart else "")
                    + f"惠及 {len(students)} 名待确认学生"
                ),
            ))
    return tuple(candidates)


def _existing_assignments(snapshot):
    accepted, planned = [], []
    for row in snapshot.rows("assignments"):
        if row["state"] == ACCEPTED:
            accepted.append(row)
        elif row["state"] == PLANNED:
            planned.append(row)
    return accepted, planned


def review_planned(tables, objectives, current_minute):
    """按当前证据复查备发安排：已补齐或已揭示冲突的，移出并写明后续核实点。"""
    snapshot = take_snapshot(tables, as_of=current_minute)
    gaps = find_gaps(tables, objectives, current_minute)
    gap_index = {gap.key: gap for gap in gaps}
    _, planned = _existing_assignments(snapshot)
    anchors = _anchor_map(snapshot)
    retained, removed = [], []
    for row in planned:
        anchor = anchors[row["anchor"]]
        related = [
            gap for gap in gaps
            if gap.goal == row["goal"]
            and (anchor["from_room"] in (gap.left_room, gap.right_room))
        ]
        if not related:
            removed.append(RemovedPlan(
                anchor=row["anchor"], goal=row["goal"], rater=row["rater"],
                reason="拟补的尺度缺口已被其他锚的评阅补齐，边已连通",
                follow_up="无需再补覆盖；仅在新评分出现极差超限时再立冲突核实",
            ))
            continue
        if any(gap.kind == CONFLICT for gap in related):
            removed.append(RemovedPlan(
                anchor=row["anchor"], goal=row["goal"], rater=row["rater"],
                reason="另一份锚已揭示该班对尺度冲突，原覆盖性安排不再合适",
                follow_up="改派第三名评委复评冲突锚，核实分歧出在哪一份证据段",
            ))
            continue
        retained.append(row)
    return tuple(retained), tuple(removed)


def _assignment_gap_keys(row, gaps, anchors, anchor_rooms, rater_room_by_rater):
    """一份已接单/备发安排实际对准的缺口键。"""
    rater_room = rater_room_by_rater.get(row["rater"])
    rooms = anchor_rooms.get(row["anchor"], set())
    keys = []
    for gap in gaps:
        if gap.goal != row["goal"]:
            continue
        edge = {gap.left_room, gap.right_room}
        if gap.kind == CONFLICT:
            if edge <= rooms:
                keys.append(gap.key)
        elif rater_room in edge:
            keys.append(gap.key)
    return keys


def plan_batch(tables, objectives, current_minute):
    """生成拟发批次：合规候选按惠及学生数贪心装入各评委剩余容量。"""
    snapshot = take_snapshot(tables, as_of=current_minute)
    gaps = find_gaps(tables, objectives, current_minute)
    candidates = find_candidates(tables, objectives, current_minute)
    accepted, _ = _existing_assignments(snapshot)
    anchors = _anchor_map(snapshot)
    rounds = _rounds(snapshot, current_minute)
    active = active_bridge_marks(snapshot)
    anchor_rooms = {}
    for mark in active:
        anchor_rooms.setdefault(mark["attempt"], set()).add(mark["room"])
    rater_room_by_rater = {rater: row["room"] for rater, row in rounds.items()}

    capacity = {rater: row["minutes"] for rater, row in rounds.items()}
    for row in accepted:
        capacity[row["rater"]] = capacity.get(row["rater"], 0) - anchors[row["anchor"]]["minutes"]

    retained, removed = review_planned(tables, objectives, current_minute)
    blocked = {(row["anchor"], row["rater"]) for row in retained}

    ordered = sorted(
        candidates,
        key=lambda c: (
            -c.weight,
            0 if all(g.kind == COVERAGE_GAP for g in c.closes_gaps) else 1,
            c.minutes,
            c.anchor,
            c.rater,
        ),
    )
    proposed = []
    fillable_gap_keys = set()
    for candidate in ordered:
        if (candidate.anchor, candidate.rater) in blocked:
            continue
        if capacity.get(candidate.rater, 0) < candidate.minutes:
            continue
        capacity[candidate.rater] -= candidate.minutes
        proposed.append(candidate)
        for gap in candidate.closes_gaps:
            fillable_gap_keys.add(gap.key)

    # 已接单或保留的备发已经承诺补证，只是评分尚未落地
    promised_gap_keys = set()
    for row in list(accepted) + list(retained):
        for key in _assignment_gap_keys(
            row, gaps, anchors, anchor_rooms, rater_room_by_rater
        ):
            promised_gap_keys.add(key)

    covered = tuple(g for g in gaps if g.key in promised_gap_keys)
    unfillable = tuple(
        gap for gap in gaps
        if gap.key not in fillable_gap_keys and gap.key not in promised_gap_keys
    )
    return BatchPlan(
        gaps=gaps,
        candidates=candidates,
        accepted=tuple(accepted),
        retained_planned=retained,
        removed_planned=removed,
        proposed=tuple(proposed),
        capacity_left=capacity,
        covered_by_assignment=covered,
        unfillable=unfillable,
    )
