"""班级报告与试算。

- 冻结报告：按会议采用的 publication 截点重放当时可见数据，结论可逐条追问，
  会后撤回/补交到的数据不会改写已引用的报告。
- 试算（what-if）：把一批"假设已到达"的桥接评分叠加到当前快照，
  只观察对仍待处理名单的影响，不落库、不改写任何已完成复核。
"""

from dataclasses import dataclass

from observation import take_snapshot
from scale import (
    link_statuses,
    room_scales,
    COVERAGE_GAP,
    STATUS_CN,
)
from dispositions import (
    reconcile,
    cross_room_ranking,
    evaluate_students,
)
from planning import plan_batch


def frozen_report(tables, publication_row, objectives, baseline_room="A"):
    """重建 publication 截点冻结的班级报告。"""
    cutoff = publication_row["cutoff"]
    frozen_tables = take_snapshot(tables, as_of=cutoff).tables
    snapshot = take_snapshot(frozen_tables, as_of=cutoff)
    return _report_payload(
        tables,
        snapshot,
        objectives,
        baseline_room,
        frozen=True,
        publication=publication_row["publication"],
        cutoff=cutoff,
    )


def current_report(tables, objectives, current_minute, baseline_room="A"):
    snapshot = take_snapshot(tables, as_of=current_minute)
    return _report_payload(
        tables,
        snapshot,
        objectives,
        baseline_room,
        frozen=False,
        publication=None,
        cutoff=current_minute,
    )


def _report_payload(tables, snapshot, objectives, baseline_room, frozen, publication, cutoff):
    work = reconcile(tables, objectives, baseline_room, current_minute=cutoff)
    statuses = link_statuses(snapshot, objectives)
    scales = room_scales(snapshot, objectives, baseline_room)
    ranking = cross_room_ranking(snapshot, objectives, baseline_room)

    scale_lines = []
    for status in statuses:
        line = {
            "goal": status.goal,
            "班对": f"{status.left_room}-{status.right_room}",
            "状态": STATUS_CN[status.status],
            "有效配对": status.pair_count,
            "差分极差": status.diff_range,
            "断开原因": status.break_reason,
            "撤回登记": list(status.withdrawn_marks),
            "证据段": [
                {
                    "锚": p.anchor,
                    "评分登记": [p.left_mark, p.right_mark],
                    "分数": [p.left_score, p.right_score],
                    "差分": p.diff,
                }
                for p in status.pairs
            ],
        }
        scale_lines.append(line)

    slips = []
    for evaluation in work.evaluations:
        slips.append({
            "student": evaluation.student,
            "goal": evaluation.goal,
            "负责老师": evaluation.owner,
            "处置": evaluation.disposition,
            "独立机会": evaluation.independent_opportunities,
            "门槛机会数": evaluation.independent_min,
            "可比评分": [
                {"登记": m.mark, "班": m.room, "分数": m.score}
                for m in evaluation.comparable_marks
            ],
            "仅班内参考评分": [
                {"登记": m.mark, "班": m.room, "分数": m.score, "断开原因": m.break_reason}
                for m in evaluation.independent_marks if not m.comparable
            ],
            "依据": list(evaluation.reasons),
            "证据登记": list(evaluation.evidence_refs),
        })

    return {
        "冻结": frozen,
        "publication": publication,
        "截点分钟": cutoff,
        "尺度边": scale_lines,
        "班尺度": {
            goal: {
                room: {
                    "可比": scale.comparable_to_baseline,
                    "偏移": scale.offset,
                    "路径": list(scale.path),
                    "冲突路径": [list(p) for p in scale.conflict_paths],
                }
                for room, scale in room_map.items()
            }
            for goal, room_map in scales.items()
        },
        "复核单": slips,
        "仍待处理": {
            "open回执": [dict(r) for r in work.open_receipts],
            "新复核单": [
                {"student": e.student, "goal": e.goal, "处置": e.disposition}
                for e in work.new_slips
            ],
        },
        "更正事项": [
            {
                "回执": c.receipt, "student": c.student, "goal": c.goal,
                "旧决定": c.old_decision, "当前裁决": c.new_disposition,
                "依据变化": c.basis_changed,
                "说明": c.reason, "证据登记": list(c.evidence_refs),
            }
            for c in work.corrections
        ],
        "已完成保留": [dict(r) for r in work.completed],
        "跨班名次": ranking,
    }


@dataclass(frozen=True)
class WhatIfResult:
    before: dict
    after: dict
    changed_keys: tuple
    note: str


def what_if(tables, objectives, current_minute, hypothetical_marks,
            baseline_room="A"):
    """叠加假设桥接评分后试算仍待处理名单。

    hypothetical_marks：与 marks 表同构的字典列表，attempt 须为桥接作答。
    安排/承诺本身不传入；只有"评分已到达"才会改变尺度与名单。
    """
    base = take_snapshot(tables, as_of=current_minute)
    before = evaluate_students(base, objectives, baseline_room)

    merged_tables = {
        name: [dict(row) for row in rows]
        for name, rows in tables.items()
    }
    merged_tables["marks"] = [
        dict(row) for row in merged_tables["marks"]
    ] + [dict(row) for row in hypothetical_marks]
    trial = take_snapshot(merged_tables, as_of=current_minute)
    after = evaluate_students(trial, objectives, baseline_room)

    before_index = {(e.student, e.goal): e for e in before}
    after_index = {(e.student, e.goal): e for e in after}
    changed = []
    for key in sorted(set(before_index) | set(after_index)):
        old = before_index.get(key)
        new = after_index.get(key)
        if old is None or new is None:
            changed.append({
                "student_goal": list(key),
                "试算前": old.disposition if old else None,
                "试算后": new.disposition if new else None,
                "依据变化": True,
                "试算后依据": list(new.reasons) if new else [],
            })
            continue
        basis_changed = old.basis_signature != new.basis_signature
        if old.disposition != new.disposition or basis_changed:
            changed.append({
                "student_goal": list(key),
                "试算前": old.disposition,
                "试算后": new.disposition,
                "依据变化": basis_changed,
                "试算后依据": list(new.reasons),
            })

    before_work = reconcile(tables, objectives, baseline_room, current_minute=current_minute)
    after_work = reconcile(merged_tables, objectives, baseline_room, current_minute=current_minute)
    return WhatIfResult(
        before={
            "open回执": [r["receipt"] for r in before_work.open_receipts],
            "新复核单": [(e.student, e.goal, e.disposition) for e in before_work.new_slips],
            "更正": [c.receipt for c in before_work.corrections],
        },
        after={
            "open回执": [r["receipt"] for r in after_work.open_receipts],
            "新复核单": [(e.student, e.goal, e.disposition) for e in after_work.new_slips],
            "更正": [c.receipt for c in after_work.corrections],
        },
        changed_keys=tuple(changed),
        note="试算仅模拟评分到达；已完成回执不复活为待办，备发安排不视为可比证据",
    )


def batch_digest(tables, objectives, current_minute):
    """供教研组比较拟发批次能补哪些分项、哪些仍需实际评阅。"""
    plan = plan_batch(tables, objectives, current_minute)
    return {
        "缺口": [
            {
                "goal": g.goal,
                "班对": f"{g.left_room}-{g.right_room}",
                "类型": "覆盖缺口" if g.kind == COVERAGE_GAP else "评委分歧",
                "待确认学生": list(g.affected_students),
                "说明": g.detail,
            }
            for g in plan.gaps
        ],
        "已接单保留": [dict(r) for r in plan.accepted],
        "备发保留": [dict(r) for r in plan.retained_planned],
        "备发移除": [
            {"锚": r.anchor, "goal": r.goal, "评委": r.rater,
             "原因": r.reason, "后续核实": r.follow_up}
            for r in plan.removed_planned
        ],
        "拟发批次": [
            {
                "锚": c.anchor, "goal": c.goal, "评委": c.rater,
                "分钟": c.minutes, "惠及学生数": c.weight,
                "补的缺口": [f"{g.left_room}-{g.right_room}" for g in c.closes_gaps],
                "需对侧评分": c.needs_counterpart, "说明": c.reason,
            }
            for c in plan.proposed
        ],
        "剩余分钟": plan.capacity_left,
        "已有安排待评分落地": [
            {"goal": g.goal, "班对": f"{g.left_room}-{g.right_room}",
             "类型": "覆盖缺口" if g.kind == COVERAGE_GAP else "评委分歧",
             "待确认学生": list(g.affected_students)}
            for g in plan.covered_by_assignment
        ],
        "仍需不同班实际评阅": [
            {"goal": g.goal, "班对": f"{g.left_room}-{g.right_room}", "说明": g.detail}
            for g in plan.unfillable
        ],
    }
