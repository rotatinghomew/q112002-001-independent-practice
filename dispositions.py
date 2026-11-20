"""独立练习复核单：裁决、回执对账与跨班名次。

三类处置严格按证据链顺序区分：
1. 补练（practice）：独立作答机会不足（不同任务上的独立机会 < independent_min）。
2. 补评（remark）：机会够，但独立作答后缺少可比较的评阅
   （无评分，或评分所在班在该 goal 上未与基准班尺度连通）。
3. 辅导（tutoring）：证据可比，但独立作答的可比分数未达 score_floor。
证据可比且达标即 pass，不发复核单。

回执对账规则：
- 已完成（state=complete）的复核不会因数据重算变成第二份待办；
  若其判断因复评/撤回失去依据，登记一条 correction（更正事项），
  旧决定保留，不改写成"当初没有发生"。
- 仍 open 的回执继续挂在当前负责老师名下，重算不产生重复待办。
"""

from dataclasses import dataclass

from observation import (
    KIND_INDEPENDENT,
    student_evidence,
    take_snapshot,
)
from scale import comparable_rooms, link_statuses, CONFLICT, COVERAGE_GAP


PRACTICE = "补练"
REMARK = "补评"
TUTORING = "辅导"
PASS = "达标"

DECISION_CN = {
    PRACTICE: "补练",
    REMARK: "补评",
    TUTORING: "辅导",
    PASS: "达标",
}


@dataclass(frozen=True)
class MarkView:
    mark: str
    attempt: str
    room: str
    goal: str
    rater: str
    score: int
    received: int
    comparable: bool
    break_reason: str


@dataclass(frozen=True)
class Evaluation:
    student: str
    goal: str
    owner: str
    independent_opportunities: int
    independent_min: int
    independent_marks: tuple[MarkView, ...]
    comparable_marks: tuple[MarkView, ...]
    disposition: str
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @property
    def basis_signature(self):
        """依据指纹：裁决所依赖的证据集合与可比状态。

        处置结论不变但依据发生变化（如某评分失去跨班可比性）时，
        指纹也会变化，据此登记更正事项而不静默重算。
        """
        return (
            self.disposition,
            self.independent_opportunities,
            tuple(sorted(
                (m.mark, m.room, m.score, m.comparable)
                for m in self.independent_marks
            )),
        )


def _marks_for_attempts(snapshot):
    index = {}
    for mark in snapshot.rows("marks"):
        index.setdefault(mark["attempt"], []).append(mark)
    return index


def evaluate_students(snapshot, objectives, baseline_room="A"):
    """对当前快照中每个 (student, goal) 给出裁决与完整证据链。"""
    obj_by_goal = {row["goal"]: row for row in snapshot.rows("objectives")}
    owner_by_student = {row["student"]: row["owner"] for row in snapshot.rows("students")}
    evidence = student_evidence(snapshot)
    comp = comparable_rooms(snapshot, objectives, baseline_room)
    statuses = link_statuses(snapshot, objectives)
    marks_by_attempt = _marks_for_attempts(snapshot)

    broken = {}
    for status in statuses:
        if status.status in (CONFLICT, COVERAGE_GAP):
            broken[status.key] = status

    evaluations = []
    for (student, goal), ev in sorted(evidence.items()):
        objective = obj_by_goal[goal]
        independent_views = [a for a in ev.attempts if a.kind == KIND_INDEPENDENT]
        mark_views = []
        for view in independent_views:
            for mark in marks_by_attempt.get(view.attempt, ()):
                room_ok = mark["room"] in comp.get(goal, set())
                reason = ""
                if not room_ok:
                    match = next(
                        (s for s in statuses
                         if s.goal == goal
                         and (mark["room"] == baseline_room
                              or baseline_room in s.key and mark["room"] in s.key)),
                        None,
                    )
                    reason = (
                        f"班 {mark['room']} 在 {goal} 上未与基准班 {baseline_room} 尺度连通"
                        + (f"：{match.break_reason}" if match and match.break_reason else "")
                    )
                mark_views.append(MarkView(
                    mark=mark["mark"],
                    attempt=mark["attempt"],
                    room=mark["room"],
                    goal=goal,
                    rater=mark["rater"],
                    score=mark["score"],
                    received=mark["received"],
                    comparable=room_ok,
                    break_reason=reason,
                ))
        comparable_marks = tuple(m for m in mark_views if m.comparable)
        refs = tuple(sorted({m.mark for m in mark_views} | {a.attempt for a in ev.attempts}))
        reasons = []

        if ev.independent_opportunities < objective["independent_min"]:
            disposition = PRACTICE
            reasons.append(
                f"独立机会 {ev.independent_opportunities} < independent_min="
                f"{objective['independent_min']}（按不同 activity 计）"
            )
            for view in ev.attempts:
                if view.kind != KIND_INDEPENDENT:
                    reasons.append(f"{view.attempt}：{'；'.join(view.reasons)}")
        elif not mark_views:
            disposition = REMARK
            reasons.append("独立作答后没有任何评阅登记")
        elif not comparable_marks:
            disposition = REMARK
            reasons.append("独立作答有评阅，但所在班级尺度未连通，缺可比较的评阅")
            for mark in mark_views:
                if not mark.comparable:
                    reasons.append(f"{mark.mark}：{mark.break_reason}")
        else:
            floor = objective["score_floor"]
            below = [m for m in comparable_marks if m.score < floor]
            if below:
                disposition = TUTORING
                reasons.append(
                    "可比独立证据 "
                    + "、".join(f"{m.mark}={m.score}" for m in comparable_marks)
                    + f" 存在低于 score_floor={floor} 的分数"
                )
            else:
                disposition = PASS
                reasons.append(
                    "可比独立证据 "
                    + "、".join(f"{m.mark}={m.score}" for m in comparable_marks)
                    + f" 均达到 score_floor={floor}"
                )

        evaluations.append(Evaluation(
            student=student,
            goal=goal,
            owner=owner_by_student.get(student, ""),
            independent_opportunities=ev.independent_opportunities,
            independent_min=objective["independent_min"],
            independent_marks=tuple(mark_views),
            comparable_marks=comparable_marks,
            disposition=disposition,
            reasons=tuple(reasons),
            evidence_refs=refs,
        ))
    return tuple(evaluations)


@dataclass(frozen=True)
class Correction:
    receipt: str
    student: str
    goal: str
    old_decision: str
    new_disposition: str
    basis_changed: bool
    reason: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ReconciledWork:
    open_receipts: tuple          # 仍待处理的既有回执
    new_slips: tuple              # 新产生的复核单
    corrections: tuple[Correction, ...]
    completed: tuple              # 已完成且依据仍成立的回执
    evaluations: tuple[Evaluation, ...]


def reconcile(tables, objectives, baseline_room="A", current_minute=None):
    """把当前裁决与既有回执对账。

    已完成回执的原始依据按其 publication 截点冻结重放：
    无论裁决结论是否改变，只要依据指纹变化，就登记更正事项。
    """
    snapshot = take_snapshot(tables, as_of=current_minute)
    evaluations = {
        (e.student, e.goal): e
        for e in evaluate_students(snapshot, objectives, baseline_room)
    }
    cutoff_by_publication = {
        row["publication"]: row["cutoff"] for row in tables["minutes"]
    }
    open_receipts, completed, corrections, new_slips = [], [], [], []
    open_keys, completed_keys = set(), set()

    for receipt in snapshot.rows("receipts"):
        key = (receipt["student"], receipt["goal"])
        latest = evaluations.get(key)
        if receipt["state"] == "complete":
            completed_keys.add(key)
            cutoff = cutoff_by_publication.get(receipt["publication"])
            frozen_snapshot = take_snapshot(tables, as_of=cutoff)
            frozen_evaluations = {
                (e.student, e.goal): e
                for e in evaluate_students(frozen_snapshot, objectives, baseline_room)
            }
            original = frozen_evaluations.get(key)
            basis_changed = (
                original is not None and latest is not None
                and original.basis_signature != latest.basis_signature
            )
            decision_changed = (
                latest is not None and latest.disposition != receipt["decision"]
            )
            if basis_changed or decision_changed:
                if decision_changed:
                    reason = (
                        f"原决定 {receipt['decision']} 的依据已变化，"
                        f"当前裁决为 {latest.disposition}；旧决定保留，仅登记更正"
                    )
                else:
                    reason = (
                        f"处置仍为 {receipt['decision']}，但依据已变化"
                        f"（如评分失去跨班可比性）；旧决定保留，登记更正供复核"
                    )
                corrections.append(Correction(
                    receipt=receipt["receipt"],
                    student=receipt["student"],
                    goal=receipt["goal"],
                    old_decision=receipt["decision"],
                    new_disposition=latest.disposition if latest else "",
                    basis_changed=basis_changed,
                    reason=reason,
                    evidence_refs=latest.evidence_refs if latest else (),
                ))
            else:
                completed.append(receipt)
        else:
            open_keys.add(key)
            open_receipts.append(receipt)

    for key, evaluation in evaluations.items():
        if key in open_keys or key in completed_keys:
            continue
        if evaluation.disposition != PASS:
            new_slips.append(evaluation)

    return ReconciledWork(
        open_receipts=tuple(open_receipts),
        new_slips=tuple(new_slips),
        corrections=tuple(corrections),
        completed=tuple(completed),
        evaluations=tuple(evaluations.values()),
    )


def cross_room_ranking(snapshot, objectives, baseline_room="A"):
    """跨班名次：只在与基准班可比的班级内、按 goal 排名。

    断开的量规项不参与跨班名次；班内原始证据仍保留在评估结果中。
    每名学生取其可比独立证据的最低分（最保守的达标判断）。
    """
    comp = comparable_rooms(snapshot, objectives, baseline_room)
    marks_by_attempt = _marks_for_attempts(snapshot)
    evidence = student_evidence(snapshot)
    ranking = {}
    for objective in objectives:
        goal = objective["goal"]
        rows = []
        for (student, g), ev in evidence.items():
            if g != goal:
                continue
            rooms = {a.room for a in ev.attempts}
            if not rooms <= comp.get(goal, set()):
                continue
            scores = []
            for view in ev.attempts:
                if view.kind != KIND_INDEPENDENT:
                    continue
                for mark in marks_by_attempt.get(view.attempt, ()):
                    if mark["room"] in comp.get(goal, set()):
                        scores.append(mark["score"])
            if not scores:
                continue
            rows.append((student, min(scores)))
        ordered = sorted(rows, key=lambda item: (-item[1], item[0]))
        ranking[goal] = tuple(
            {"rank": index + 1, "student": student, "score": score}
            for index, (student, score) in enumerate(ordered)
        )
    return ranking
