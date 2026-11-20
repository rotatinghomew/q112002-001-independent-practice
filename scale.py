"""跨班尺度桥接。

桥接（bridge）作答不是学生表现：两位教师按各自班口径对同一份锚作答评分。
本模块只依据桥接评分判断"哪些班在哪个量规分项上尺度可比"，不借项：
每个 goal 独立成图，共同作业只覆盖一部分分项时，可比性不外溢到其他分项。

判断口径（每个 goal、每对班级一条边）：
- 撤回通知到达后，对应桥接评分登记立即失效。
- 有效配对数 < objective.bridge_min：coverage_gap（覆盖缺口，可补证恢复）。
- 配对评分之差的极差 > objective.gap_limit：conflict（评委分歧，需复评核实）。
- 达到 bridge_min 且极差不超限：linked，并记录班际偏移（他班 - A 班口径）。
- 跨多对班级出现互相矛盾的尺度关系时标 conflict，不选任意一条路径。
"""

from dataclasses import dataclass


LINKED = "linked"
COVERAGE_GAP = "coverage_gap"
CONFLICT = "conflict"

STATUS_CN = {
    LINKED: "尺度已连通",
    COVERAGE_GAP: "覆盖缺口",
    CONFLICT: "评委分歧待复评",
}


def is_bridge_attempt(attempt_id):
    return str(attempt_id).startswith("b")


def withdrawn_marks(snapshot):
    return {row["mark"] for row in snapshot.rows("withdrawals")}


def active_bridge_marks(snapshot):
    """有效桥接评分：桥接 attempt、未被撤回。"""
    withdrawn = withdrawn_marks(snapshot)
    return [
        row for row in snapshot.rows("marks")
        if is_bridge_attempt(row["attempt"]) and row["mark"] not in withdrawn
    ]


@dataclass(frozen=True)
class PairEvidence:
    goal: str
    left_room: str
    right_room: str
    anchor: str
    left_mark: str
    right_mark: str
    left_score: int
    right_score: int
    diff: int  # right_score - left_score

    @property
    def key(self):
        return tuple(sorted((self.left_room, self.right_room)))


def bridge_pairs(snapshot, goal):
    """同一锚作答上两个班的有效评分配对，按班级对归一化排序。"""
    marks = [m for m in active_bridge_marks(snapshot) if m["goal"] == goal]
    by_anchor = {}
    for mark in marks:
        by_anchor.setdefault(mark["attempt"], []).append(mark)

    pairs = []
    for anchor, anchor_marks in by_anchor.items():
        for i in range(len(anchor_marks)):
            for j in range(i + 1, len(anchor_marks)):
                a, b = anchor_marks[i], anchor_marks[j]
                if a["room"] == b["room"]:
                    continue
                left, right = sorted((a, b), key=lambda m: m["room"])
                pairs.append(
                    PairEvidence(
                        goal=goal,
                        left_room=left["room"],
                        right_room=right["room"],
                        anchor=anchor,
                        left_mark=left["mark"],
                        right_mark=right["mark"],
                        left_score=left["score"],
                        right_score=right["score"],
                        diff=right["score"] - left["score"],
                    )
                )
    return tuple(pairs)


@dataclass(frozen=True)
class LinkStatus:
    goal: str
    left_room: str
    right_room: str
    status: str
    pair_count: int
    diff_range: int | None
    offset: float | None          # linked 时的配对差分中位数（right-left）
    pairs: tuple[PairEvidence, ...]
    withdrawn_marks: tuple[str, ...]
    break_reason: str

    @property
    def key(self):
        return tuple(sorted((self.left_room, self.right_room)))


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def link_statuses(snapshot, objectives):
    """每个 goal 上每个出现过的班级对的尺度边状态。"""
    withdrawn = withdrawn_marks(snapshot)
    statuses = []
    for objective in objectives:
        goal = objective["goal"]
        pairs = bridge_pairs(snapshot, goal)
        edges = {}
        for pair in pairs:
            edges.setdefault(pair.key, []).append(pair)
        # 撤回可能让某班级对暂时没有任何有效配对，但边仍需留痕
        goal_bridge_marks = [
            m for m in snapshot.rows("marks")
            if m["goal"] == goal and is_bridge_attempt(m["attempt"])
        ]
        anchors_seen = {m["attempt"] for m in goal_bridge_marks}
        for anchor in anchors_seen:
            anchor_marks = [m for m in goal_bridge_marks if m["attempt"] == anchor]
            for i in range(len(anchor_marks)):
                for j in range(i + 1, len(anchor_marks)):
                    a, b = anchor_marks[i], anchor_marks[j]
                    if a["room"] != b["room"]:
                        edges.setdefault(tuple(sorted((a["room"], b["room"]))), [])
        for key, edge_pairs in sorted(edges.items()):
            left_room, right_room = key
            edge_withdrawn = tuple(sorted(
                m["mark"] for m in goal_bridge_marks
                if m["mark"] in withdrawn and m["room"] in (left_room, right_room)
                and any(
                    peer["attempt"] == m["attempt"]
                    and peer["room"] in (left_room, right_room)
                    and peer["room"] != m["room"]
                    for peer in goal_bridge_marks
                )
            ))
            pair_count = len(edge_pairs)
            if pair_count < objective["bridge_min"]:
                statuses.append(LinkStatus(
                    goal=goal,
                    left_room=left_room,
                    right_room=right_room,
                    status=COVERAGE_GAP,
                    pair_count=pair_count,
                    diff_range=None,
                    offset=None,
                    pairs=tuple(edge_pairs),
                    withdrawn_marks=edge_withdrawn,
                    break_reason=(
                        f"有效共同评阅 {pair_count} 份，低于 bridge_min="
                        f"{objective['bridge_min']}"
                    ),
                ))
                continue
            diffs = [p.diff for p in edge_pairs]
            diff_range = max(diffs) - min(diffs)
            if diff_range > objective["gap_limit"]:
                statuses.append(LinkStatus(
                    goal=goal,
                    left_room=left_room,
                    right_room=right_room,
                    status=CONFLICT,
                    pair_count=pair_count,
                    diff_range=diff_range,
                    offset=None,
                    pairs=tuple(edge_pairs),
                    withdrawn_marks=edge_withdrawn,
                    break_reason=(
                        f"配对差分之极差 {diff_range} 超过 gap_limit="
                        f"{objective['gap_limit']}；证据段："
                        + "、".join(f"{p.anchor}({p.left_mark}/{p.right_mark})" for p in edge_pairs)
                    ),
                ))
                continue
            statuses.append(LinkStatus(
                goal=goal,
                left_room=left_room,
                right_room=right_room,
                status=LINKED,
                pair_count=pair_count,
                diff_range=diff_range,
                offset=_median(diffs),
                pairs=tuple(edge_pairs),
                withdrawn_marks=edge_withdrawn,
                break_reason="",
            ))
    return tuple(statuses)


@dataclass(frozen=True)
class RoomScale:
    room: str
    comparable_to_baseline: bool
    offset: float | None
    path: tuple[str, ...]
    conflict_paths: tuple[tuple[str, ...], ...]


def _room_edges(statuses):
    edges = {}
    for status in statuses:
        if status.status == LINKED:
            edges[frozenset(status.key)] = status
    return edges


def room_scales(snapshot, objectives, baseline_room="A"):
    """从基准班出发求每个班在每个 goal 上的尺度位置。

    多条路径推出互相矛盾的偏移时，该班标为不可比并保留冲突路径，
    不任意选择一条路径。偏移以"他班分数 - 基准班分数"表示。
    """
    result = {}
    for objective in objectives:
        goal = objective["goal"]
        statuses = [s for s in link_statuses(snapshot, [objective])]
        edges = _room_edges(statuses)
        adjacency = {}
        for edge_set, status in edges.items():
            a, b = tuple(edge_set)
            adjacency.setdefault(a, []).append((b, status))
            adjacency.setdefault(b, []).append((a, status))

        # DFS 枚举基准班到每个班的全部简单路径
        all_paths = {}

        def walk(node, path, offset_total):
            for nxt, status in adjacency.get(node, ()):
                if nxt in path:
                    continue
                step = status.offset if node == status.left_room else -status.offset
                new_total = offset_total + step
                new_path = path + (nxt,)
                all_paths.setdefault(nxt, []).append((new_path, new_total))
                walk(nxt, new_path, new_total)

        walk(baseline_room, (baseline_room,), 0.0)

        goal_scales = {
            baseline_room: RoomScale(
                room=baseline_room,
                comparable_to_baseline=True,
                offset=0.0,
                path=(baseline_room,),
                conflict_paths=(),
            )
        }
        rooms = {s.left_room for s in statuses} | {s.right_room for s in statuses}
        for room in sorted(rooms):
            if room == baseline_room:
                continue
            routes = all_paths.get(room, [])
            if not routes:
                goal_scales[room] = RoomScale(
                    room=room, comparable_to_baseline=False, offset=None,
                    path=(), conflict_paths=(),
                )
                continue
            offsets = [round(total, 9) for _, total in routes]
            tolerance = 1e-9
            consistent = max(offsets) - min(offsets) <= tolerance
            if consistent:
                path, total = routes[0]
                goal_scales[room] = RoomScale(
                    room=room, comparable_to_baseline=True, offset=total,
                    path=path, conflict_paths=(),
                )
            else:
                goal_scales[room] = RoomScale(
                    room=room, comparable_to_baseline=False, offset=None,
                    path=(),
                    conflict_paths=tuple(path for path, _ in routes),
                )
        result[goal] = goal_scales
    return result


def comparable_rooms(snapshot, objectives, baseline_room="A"):
    """{goal: set(与基准班尺度可比的班级)}，基准班自身恒在集合内。"""
    scales = room_scales(snapshot, objectives, baseline_room)
    return {
        goal: {room for room, scale in room_map.items() if scale.comparable_to_baseline}
        for goal, room_map in scales.items()
    }
