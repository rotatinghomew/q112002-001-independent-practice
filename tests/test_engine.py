"""引擎业务规则测试：用合成登记构造边界场景。"""

import copy
import unittest

from record_reader import read_records
from observation import (
    take_snapshot,
    classify_attempts,
    student_evidence,
    KIND_INDEPENDENT,
    KIND_ASSISTED,
    KIND_UNVERIFIABLE,
    KIND_UNRESOLVED,
)
from scale import (
    link_statuses,
    room_scales,
    comparable_rooms,
    bridge_pairs,
    LINKED,
    COVERAGE_GAP,
    CONFLICT,
)
from dispositions import (
    evaluate_students,
    reconcile,
    cross_room_ranking,
    PRACTICE,
    REMARK,
    TUTORING,
    PASS,
)
from planning import plan_batch, find_gaps, find_candidates
from reports import frozen_report, what_if


def base_tables():
    return copy.deepcopy(read_records())


def objectives(tables):
    return tables["objectives"]


class IndependenceTest(unittest.TestCase):
    def test_timed_hint_only_taints_next_attempt(self):
        # s1：a1(10) < 提示(30) < a2(35)，再无提示；a3(50) 恢复独立
        tables = base_tables()
        snap = take_snapshot(tables, 110)
        kinds = classify_attempts(snap)
        self.assertEqual(kinds["a1"].kind, KIND_INDEPENDENT)
        self.assertEqual(kinds["a2"].kind, KIND_ASSISTED)
        self.assertEqual(kinds["a3"].kind, KIND_INDEPENDENT)

    def test_untimed_hint_blocks_all_independence(self):
        # s4 有 minute=None 的提示：a8 不可核实，a7 是 unresolved
        tables = base_tables()
        snap = take_snapshot(tables, 110)
        kinds = classify_attempts(snap)
        self.assertEqual(kinds["a7"].kind, KIND_UNRESOLVED)
        self.assertEqual(kinds["a8"].kind, KIND_UNVERIFIABLE)
        ev = student_evidence(snap)[("s4", "reason")]
        self.assertEqual(ev.independent_opportunities, 0)

    def test_redo_on_same_activity_counts_once(self):
        tables = base_tables()
        tables["hints"] = [h for h in tables["hints"] if h["hint"] != "h2"]
        # 去掉原有 a8，只留同一 activity z9 上的两次独立重做
        tables["attempts"] = [
            a for a in tables["attempts"] if a["attempt"] not in ("a7", "a8")
        ] + [
            {"attempt": "r1", "student": "s4", "room": "A", "goal": "reason",
             "activity": "z9", "stage": 1, "minute": 80, "channel": "independent"},
            {"attempt": "r2", "student": "s4", "room": "A", "goal": "reason",
             "activity": "z9", "stage": 1, "minute": 85, "channel": "independent"},
        ]
        ev = student_evidence(take_snapshot(tables, 110))[("s4", "reason")]
        self.assertEqual(ev.independent_opportunities, 1)
        self.assertEqual(
            [a.attempt for a in ev.attempts if a.kind == KIND_INDEPENDENT],
            ["r1", "r2"],
        )

    def test_hint_scoped_by_stage_student_goal(self):
        tables = base_tables()
        tables["hints"].append(
            {"hint": "hx", "student": "s2", "goal": "counter",
             "stage": 2, "minute": 5})
        snap = take_snapshot(tables, 110)
        kinds = classify_attempts(snap)
        # s2 的 reason 作答不受另一目标的提示影响
        self.assertEqual(kinds["a4"].kind, KIND_INDEPENDENT)
        self.assertEqual(kinds["a5"].kind, KIND_INDEPENDENT)


class ScaleBridgeTest(unittest.TestCase):
    def test_withdrawal_breaks_only_affected_edge(self):
        tables = base_tables()
        snap = take_snapshot(tables, 110)
        statuses = {
            (s.goal, s.key): s for s in link_statuses(snap, objectives(tables))
        }
        self.assertEqual(statuses[("reason", ("A", "B"))].status, LINKED)
        self.assertEqual(statuses[("reason", ("B", "C"))].status, CONFLICT)
        self.assertIn("b4c", statuses[("reason", ("B", "C"))].withdrawn_marks)
        self.assertNotIn("b4c", statuses[("reason", ("A", "B"))].withdrawn_marks)

    def test_before_withdrawal_edge_was_linked(self):
        tables = base_tables()
        snap = take_snapshot(tables, 90)  # w1 在 101，尚未到达
        statuses = {
            (s.goal, s.key): s for s in link_statuses(snap, objectives(tables))
        }
        self.assertEqual(statuses[("reason", ("B", "C"))].status, LINKED)
        self.assertEqual(statuses[("reason", ("B", "C"))].pair_count, 2)

    def test_withdrawal_drops_pair_count_to_coverage_gap(self):
        tables = base_tables()
        # 删掉 b6 两个评分，撤回后 B-C 只剩 b3 一个有效配对
        tables["marks"] = [
            m for m in tables["marks"] if m["attempt"] != "b6"
        ]
        snap = take_snapshot(tables, 110)
        statuses = {
            (s.goal, s.key): s for s in link_statuses(snap, objectives(tables))
        }
        self.assertEqual(statuses[("reason", ("B", "C"))].status, COVERAGE_GAP)
        self.assertEqual(statuses[("reason", ("B", "C"))].pair_count, 1)

    def test_goal_comparability_does_not_transfer(self):
        tables = base_tables()
        comp = comparable_rooms(take_snapshot(tables, 110), objectives(tables))
        # reason 上 B 连通，counter 上没有 B 的任何桥接
        self.assertIn("B", comp["reason"])
        self.assertNotIn("B", comp["counter"])

    def test_coverage_gap_counter_edge(self):
        tables = base_tables()
        statuses = {
            (s.goal, s.key): s
            for s in link_statuses(take_snapshot(tables, 110), objectives(tables))
        }
        self.assertEqual(statuses[("counter", ("A", "C"))].status, COVERAGE_GAP)

    def test_conflicting_multipath_marked_not_arbitrary(self):
        tables = base_tables()
        # 撤回清除，移除让 B-C 冲突的 b6，使 B-C 回到连通（仅 b3，差分 0）
        tables["withdrawals"] = []
        tables["marks"] = [m for m in tables["marks"] if m["attempt"] != "b6"]
        # 新增 A-C 直连，偏移 -4，与经 B 推出的 +1 矛盾
        tables["marks"].extend([
            {"mark": "x1a", "attempt": "b9", "room": "A", "goal": "reason",
             "rater": "甲", "score": 4, "received": 105},
            {"mark": "x1c", "attempt": "b9", "room": "C", "goal": "reason",
             "rater": "丙", "score": 0, "received": 105},
            {"mark": "x2a", "attempt": "b10", "room": "A", "goal": "reason",
             "rater": "甲", "score": 4, "received": 105},
            {"mark": "x2c", "attempt": "b10", "room": "C", "goal": "reason",
             "rater": "丙", "score": 0, "received": 105},
        ])
        snap = take_snapshot(tables, 110)
        statuses = {(s.goal, s.key): s
                    for s in link_statuses(snap, objectives(tables))}
        self.assertEqual(statuses[("reason", ("B", "C"))].status, LINKED)
        scales = room_scales(snap, objectives(tables))["reason"]
        self.assertFalse(scales["C"].comparable_to_baseline)
        self.assertTrue(scales["C"].conflict_paths)


class DispositionTest(unittest.TestCase):
    def test_three_way_dispositions_on_fixture(self):
        tables = base_tables()
        snap = take_snapshot(tables, 110)
        result = {
            (e.student, e.goal): e
            for e in evaluate_students(snap, objectives(tables))
        }
        self.assertEqual(result[("s4", "reason")].disposition, PRACTICE)
        self.assertEqual(result[("s2", "reason")].disposition, TUTORING)
        self.assertEqual(result[("s3", "counter")].disposition, PASS)

    def test_remark_when_marks_missing(self):
        tables = base_tables()
        tables["marks"] = [m for m in tables["marks"] if m["attempt"] != "a4"]
        # a5 仍有一份评分，所以不是完全缺评；改成两份都删
        tables["marks"] = [
            m for m in tables["marks"] if m["attempt"] not in ("a4", "a5")
        ]
        snap = take_snapshot(tables, 110)
        result = {
            (e.student, e.goal): e
            for e in evaluate_students(snap, objectives(tables))
        }
        self.assertEqual(result[("s2", "reason")].disposition, REMARK)

    def test_remark_when_room_scale_broken(self):
        tables = base_tables()
        # s3 的 counter 只有 A 班评分；让 A-C counter 仍缺口时 A 自身仍可比，
        # 改为把 s3 转到 C 班口径作答来触发断开
        tables["attempts"] = [
            dict(a, room="C") if a["attempt"] == "a6" else a
            for a in tables["attempts"]
        ]
        tables["marks"] = [
            dict(m, room="C") if m["attempt"] == "a6" else m
            for m in tables["marks"]
        ]
        snap = take_snapshot(tables, 110)
        result = {
            (e.student, e.goal): e
            for e in evaluate_students(snap, objectives(tables))
        }
        self.assertEqual(result[("s3", "counter")].disposition, REMARK)

    def test_reconcile_completed_never_reopens_but_corrects(self):
        tables = base_tables()
        work = reconcile(tables, objectives(tables), current_minute=110)
        receipt_ids_open = {r["receipt"] for r in work.open_receipts}
        receipt_ids_done = {r["receipt"] for r in work.completed}
        correction_ids = {c.receipt for c in work.corrections}
        self.assertIn("f2", receipt_ids_open)       # open 不重复发单
        self.assertNotIn("f1", receipt_ids_open)    # complete 不复活
        self.assertNotIn("f1", receipt_ids_done)    # 依据变了，进更正
        self.assertIn("f1", correction_ids)
        # 更正里旧决定保留
        correction = next(c for c in work.corrections if c.receipt == "f1")
        self.assertEqual(correction.old_decision, "辅导")
        self.assertTrue(correction.basis_changed)

    def test_no_duplicate_slip_on_recompute(self):
        tables = base_tables()
        first = reconcile(tables, objectives(tables), current_minute=110)
        second = reconcile(tables, objectives(tables), current_minute=110)
        self.assertEqual(
            {(e.student, e.goal) for e in first.new_slips},
            {(e.student, e.goal) for e in second.new_slips},
        )

    def test_ranking_excludes_broken_rooms_keeps_classroom_evidence(self):
        tables = base_tables()
        ranking = cross_room_ranking(take_snapshot(tables, 110), objectives(tables))
        reason_students = {row["student"] for row in ranking["reason"]}
        # s1 在 C 班有作答而 C 断开，整体不进跨班名次
        self.assertNotIn("s1", reason_students)
        self.assertIn("s2", reason_students)
        # 但 s1 的班内评分仍在评估结果里
        snap = take_snapshot(tables, 110)
        s1 = next(e for e in evaluate_students(snap, objectives(tables))
                  if e.student == "s1")
        self.assertTrue(any(not m.comparable for m in s1.independent_marks))


class FrozenAndWhatIfTest(unittest.TestCase):
    def test_frozen_report_matches_cutoff(self):
        tables = base_tables()
        minute_row = tables["minutes"][0]
        report = frozen_report(tables, minute_row, objectives(tables))
        self.assertTrue(report["冻结"])
        edge = next(
            e for e in report["尺度边"]
            if e["goal"] == "reason" and e["班对"] == "B-C"
        )
        self.assertEqual(edge["状态"], "尺度已连通")
        # 截点后到达的回执不出现在冻结报告
        self.assertEqual(report["仍待处理"]["open回执"], [])

    def test_whatif_does_not_persist_or_reopen(self):
        tables = base_tables()
        hypothetical = [
            {"mark": "hb1c", "attempt": "b1", "room": "C", "goal": "reason",
             "rater": "丙", "score": 1, "received": 108},
        ]
        result = what_if(tables, objectives(tables), 110, hypothetical)
        # 原始表未被写入
        self.assertFalse(
            any(m["mark"] == "hb1c" for m in base_tables()["marks"])
        )
        # 已完成回执在试算前后都不进待办
        self.assertNotIn("f1", result.before["open回执"])
        self.assertNotIn("f1", result.after["open回执"])


class BatchPlanningTest(unittest.TestCase):
    def test_gap_classification_and_affected_students(self):
        tables = base_tables()
        gaps = {g.key: g for g in find_gaps(tables, objectives(tables), 110)}
        conflict = gaps[("reason", tuple(sorted(("B", "C"))))]
        coverage = gaps[("counter", tuple(sorted(("A", "C"))))]
        self.assertEqual(conflict.kind, CONFLICT)
        self.assertEqual(set(conflict.affected_students), {"s1", "s2"})
        self.assertEqual(coverage.kind, COVERAGE_GAP)

    def test_candidate_authorization_rules(self):
        tables = base_tables()
        candidates = find_candidates(tables, objectives(tables), 110)
        for candidate in candidates:
            anchor = next(a for a in tables["anchors"]
                          if a["anchor"] == candidate.anchor)
            rater_round = next(r for r in tables["review_rounds"]
                               if r["rater"] == candidate.rater)
            self.assertNotEqual(candidate.rater, anchor["first_rater"])
            self.assertNotEqual(rater_round["room"], anchor["from_room"])
            self.assertIn(candidate.goal, rater_round["goals"])
            # 没读过：有效评分里不存在该评委对该锚的登记
            self.assertFalse(any(
                m["attempt"] == candidate.anchor and m["rater"] == candidate.rater
                for m in tables["marks"]
            ))

    def test_accepted_work_retained_and_consumes_capacity(self):
        tables = base_tables()
        plan = plan_batch(tables, objectives(tables), 110)
        accepted_raters = {row["rater"] for row in plan.accepted}
        self.assertIn("丙", accepted_raters)
        # 丙容量 15，接单 b6=11 分钟，剩 4
        self.assertEqual(plan.capacity_left["丙"], 4)

    def test_anchor_minutes_counted_once_per_rater(self):
        tables = base_tables()
        plan = plan_batch(tables, objectives(tables), 110)
        # 甲拟发的同一锚只扣一次分钟
        jia = [c for c in plan.proposed if c.rater == "甲"]
        anchors = [c.anchor for c in jia]
        self.assertEqual(len(anchors), len(set(anchors)))
        spent = sum(c.minutes for c in jia)
        self.assertEqual(plan.capacity_left["甲"], 24 - spent)

    def test_planned_removed_after_conflict_revealed(self):
        tables = base_tables()
        plan = plan_batch(tables, objectives(tables), 110)
        removed = {(r.anchor, r.rater) for r in plan.removed_planned}
        self.assertIn(("b7", "甲"), removed)
        retained = {(r["anchor"], r["rater"]) for r in plan.retained_planned}
        self.assertIn(("b8", "甲"), retained)
        # 移除的安排不会重新出现在拟发批次
        self.assertFalse(
            any(c.anchor == "b7" and c.rater == "甲" for c in plan.proposed)
        )

    def test_unready_anchor_not_offered(self):
        tables = base_tables()
        # 把 b3 的 ready 推到当前分钟之后
        tables["anchors"] = [
            dict(a, ready=500) if a["anchor"] == "b3" else a
            for a in tables["anchors"]
        ]
        candidates = find_candidates(tables, objectives(tables), 110)
        self.assertFalse(any(c.anchor == "b3" for c in candidates))

    def test_conflict_needs_neutral_rater_coverage_needs_edge_room(self):
        tables = base_tables()
        plan = plan_batch(tables, objectives(tables), 110)
        # B-C 冲突锚 b3/b6 只派 A 班评委甲（中立），不会派乙或丙
        conflict_candidates = [
            c for c in plan.candidates
            if any(g.kind == CONFLICT for g in c.closes_gaps)
        ]
        self.assertTrue(conflict_candidates)
        for candidate in conflict_candidates:
            self.assertEqual(candidate.rater, "甲")
        # 已承诺与无候选分档：两个缺口都已有安排或可派，不存在无候选
        covered = {g.key for g in plan.covered_by_assignment}
        self.assertIn(("reason", tuple(sorted(("B", "C")))), covered)
        self.assertIn(("counter", tuple(sorted(("A", "C")))), covered)
        self.assertEqual(plan.unfillable, ())

    def test_withdrawal_rechecks_ranking_and_slips_together(self):
        tables = base_tables()
        # 撤回前（截点90）：C 连通，s1 进跨班名次且无更正
        before = reconcile(tables, objectives(tables), current_minute=90)
        self.assertEqual(before.corrections, ())
        ranking_before = cross_room_ranking(
            take_snapshot(tables, 90), objectives(tables)
        )
        self.assertIn("s1", {r["student"] for r in ranking_before["reason"]})
        # 撤回后（110）：C 断开，s1 出名次，已完成 f1 产生更正，且不复活待办
        after = reconcile(tables, objectives(tables), current_minute=110)
        ranking_after = cross_room_ranking(
            take_snapshot(tables, 110), objectives(tables)
        )
        self.assertNotIn("s1", {r["student"] for r in ranking_after["reason"]})
        self.assertTrue(any(c.receipt == "f1" for c in after.corrections))
        self.assertFalse(
            any(r["receipt"] == "f1" for r in after.open_receipts)
        )
        # 班内原始证据没有随跨班比较消失
        s1 = next(e for e in after.evaluations if e.student == "s1")
        self.assertTrue(any(not m.comparable for m in s1.independent_marks))


if __name__ == "__main__":
    unittest.main()
