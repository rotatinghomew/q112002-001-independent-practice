"""证据快照与独立性判定。

只负责登记数据的时间裁剪和"独立/辅助"分类，不做任何跨班尺度判断。

独立性规则（对应教研组约定）：
- 提示只适用于本阶段、同一学生与同一目标。
- 有确定时刻的提示，只"污染"它之后的下一次作答；再下一次作答恢复独立。
- 提示时刻未知（minute=None）时，无法确认任何一次作答在提示之前，
  因此该学生该目标该阶段的全部作答都不能作为独立证据。
- channel 为 unresolved 的登记本身不是作答机会。
- 多次重做（同一 activity）与新目标/新任务的首次观察分开统计：
  独立机会按不同 activity 计数，同一 activity 上的重做不增加机会数。
"""

from dataclasses import dataclass, field


# channel 取值
INDEPENDENT = "independent"
ASSISTED = "assisted"
UNRESOLVED = "unresolved"

# 分类结论
KIND_INDEPENDENT = "independent"        # 经提示记录核实的独立作答
KIND_ASSISTED = "assisted"              # 带提示的作答
KIND_UNRESOLVED = "unresolved"          # 未形成作答
KIND_UNVERIFIABLE = "unverifiable"      # 提示时刻未知，无法核实独立性


@dataclass(frozen=True)
class Snapshot:
    """某个时间点上可见的全部登记。

    所有登记都按各自的"到达/发生分钟"裁剪：attempt、hint 按 minute，
    mark 按 received，withdrawal 按 received，receipt 按 minute，
    assignment 按 minute，review_round 按 minute。
    """

    as_of: int | None
    tables: dict

    def rows(self, name):
        return list(self.tables.get(name, ()))


def take_snapshot(tables, as_of=None):
    """从原始登记表切出 as_of 时刻可见的子集（不修改入参）。"""
    visible = {}
    time_field = {
        "attempts": "minute",
        "hints": "minute",
        "marks": "received",
        "withdrawals": "received",
        "receipts": "minute",
        "assignments": "minute",
        "review_rounds": "minute",
    }
    for name, rows in tables.items():
        field_name = time_field.get(name)
        if field_name is None or as_of is None:
            visible[name] = [dict(row) for row in rows]
            continue
        kept = []
        for row in rows:
            moment = row[field_name]
            if moment is None or moment <= as_of:
                kept.append(dict(row))
        visible[name] = kept
    return Snapshot(as_of=as_of, tables=visible)


@dataclass(frozen=True)
class AttemptView:
    attempt: str
    student: str
    room: str
    goal: str
    activity: str
    stage: int
    minute: int
    channel: str
    kind: str
    reasons: tuple = ()


@dataclass(frozen=True)
class StudentEvidence:
    student: str
    goal: str
    attempts: tuple[AttemptView, ...]
    independent_activities: tuple[str, ...]
    assisted_activities: tuple[str, ...]

    @property
    def independent_opportunities(self):
        """独立机会数：经核实的独立作答所覆盖的不同任务（activity）数。"""
        return len(self.independent_activities)


def _classify_group(attempt_rows, timed_hints, has_untimed_hint):
    """对同一 (student, goal, stage) 内的作答做分类。"""
    ordered = sorted(attempt_rows, key=lambda row: row["minute"])
    # 提示按时刻排队，每个提示只作用于它之后的下一次作答
    hint_minutes = sorted(h["minute"] for h in timed_hints)
    views = []
    hint_index = 0
    for row in ordered:
        reasons = []
        if row["channel"] == UNRESOLVED:
            kind = KIND_UNRESOLVED
            reasons.append("channel=unresolved，未形成作答")
        elif has_untimed_hint:
            kind = KIND_UNVERIFIABLE
            reasons.append("存在时刻未知的提示，无法确认作答独立")
        else:
            tainted = False
            while hint_index < len(hint_minutes) and hint_minutes[hint_index] < row["minute"]:
                hint_index += 1
                tainted = True
            if tainted:
                kind = KIND_ASSISTED
                reasons.append("紧邻其前的提示只影响本次作答的独立性")
            elif row["channel"] == INDEPENDENT:
                kind = KIND_INDEPENDENT
                reasons.append("自报独立且无提示落在本次作答之前")
            else:
                kind = KIND_ASSISTED
                reasons.append(f"channel={row['channel']}，非独立登记")
        views.append(
            AttemptView(
                attempt=row["attempt"],
                student=row["student"],
                room=row["room"],
                goal=row["goal"],
                activity=row["activity"],
                stage=row["stage"],
                minute=row["minute"],
                channel=row["channel"],
                kind=kind,
                reasons=tuple(reasons),
            )
        )
    return views


def classify_attempts(snapshot):
    """返回 {attempt_id: AttemptView}，含全部可见作答的独立性结论。"""
    timed, untimed_keys = [], set()
    for hint in snapshot.rows("hints"):
        if hint["minute"] is None:
            untimed_keys.add((hint["student"], hint["goal"], hint["stage"]))
        else:
            timed.append(hint)

    groups = {}
    for row in snapshot.rows("attempts"):
        groups.setdefault((row["student"], row["goal"], row["stage"]), []).append(row)

    views = {}
    for key, rows in groups.items():
        group_timed = [
            h for h in timed
            if (h["student"], h["goal"], h["stage"]) == key
        ]
        for view in _classify_group(rows, group_timed, key in untimed_keys):
            views[view.attempt] = view
    return views


def student_evidence(snapshot):
    """按 (student, goal) 汇总证据，跨班、跨阶段合并，重做与新任务分开。"""
    views = classify_attempts(snapshot)
    bucket = {}
    for view in views.values():
        bucket.setdefault((view.student, view.goal), []).append(view)

    result = {}
    for (student, goal), group_views in bucket.items():
        independent = sorted({
            view.activity for view in group_views if view.kind == KIND_INDEPENDENT
        })
        assisted = sorted({
            view.activity
            for view in group_views
            if view.kind in (KIND_ASSISTED, KIND_UNVERIFIABLE)
        })
        result[(student, goal)] = StudentEvidence(
            student=student,
            goal=goal,
            attempts=tuple(sorted(group_views, key=lambda v: (v.stage, v.minute))),
            independent_activities=tuple(independent),
            assisted_activities=tuple(assisted),
        )
    return result
