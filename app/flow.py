"""最大流 / 最小割引擎、检修前审计与薄弱管段复核。

规则（对应业务要求）：

* 每条管段被视作一条有向边，录入的最大流量即其容量上限；
* 在**正常网络**与**每一条可检修管段单独移除后的残余网络**上，
  分别独立运行最大流（Dinic），互不复用中间流量结果；
* 事故要求的持续排出流量必须在正常网络和每一个单点失效情景下都可达，
  审计才放行；
* 失效时按管段录入顺序返回第一条不达标管段，并依据最大流 / 最小割定理，
  从残余网络给出可复核的源侧割集、焚烧端侧节点及割集容量；
* 审计通过后可发起**薄弱管段复核**：服务端从完整草稿**重新执行**上述
  审计，并对每个达标情形判定每条管段在**全部**容量等于该情形最大可
  导排量的源侧最小割中的归属（从不跨割 / 仅部分最小割跨越 / 全部最小
  割均跨越），同时给出最小割容量与相对事故要求流量的裕量——而不是只
  凭一次最大流运行恰好返回的那一个割集下结论。

注意：本模块用“流量”而不是“路径条数”下结论——存在多条路径并不保证
总排量达标，共享瓶颈会限制总流量。
"""
from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from typing import Optional

# 检修网络节点规模通常不大，放宽递归深度以支持较长的增广链。
sys.setrecursionlimit(100_000)

EPS = 1e-9


class NetworkValidationError(ValueError):
    """网络输入无效（节点引用、容量、方向等业务校验失败）。"""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass
class _Edge:
    """Dinic 内部边（带反向残量边索引）。"""

    to: int
    rev: int
    cap: float


class Dinic:
    """容量为非负实数的有向图 Dinic 最大流。"""

    def __init__(self, n: int):
        self.n = n
        self.g: list[list[_Edge]] = [[] for _ in range(n)]

    def add_edge(self, u: int, v: int, cap: float) -> None:
        fwd = _Edge(to=v, rev=len(self.g[v]), cap=float(cap))
        bak = _Edge(to=u, rev=len(self.g[u]), cap=0.0)
        self.g[u].append(fwd)
        self.g[v].append(bak)

    def _bfs(self, s: int, t: int) -> list[int]:
        level = [-1] * self.n
        level[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for e in self.g[u]:
                if e.cap > EPS and level[e.to] < 0:
                    level[e.to] = level[u] + 1
                    q.append(e.to)
        return level

    def _dfs(self, u: int, t: int, pushed: float, level: list[int], it: list[int]) -> float:
        if u == t:
            return pushed
        while it[u] < len(self.g[u]):
            e = self.g[u][it[u]]
            if e.cap > EPS and level[e.to] == level[u] + 1:
                got = self._dfs(e.to, t, min(pushed, e.cap), level, it)
                if got > EPS:
                    e.cap -= got
                    self.g[e.to][e.rev].cap += got
                    return got
            it[u] += 1
        return 0.0

    def max_flow(self, s: int, t: int) -> float:
        flow = 0.0
        inf = float("inf")
        while True:
            level = self._bfs(s, t)
            if level[t] < 0:
                return flow
            it = [0] * self.n
            while True:
                pushed = self._dfs(s, t, inf, level, it)
                if pushed <= EPS:
                    break
                flow += pushed

    def reachable_set(self, start: int) -> list[bool]:
        """沿残余容量 > 0 的边做 BFS，得到从 start 残余可达的节点集合。"""
        seen = [False] * self.n
        seen[start] = True
        q = deque([start])
        while q:
            u = q.popleft()
            for e in self.g[u]:
                if e.cap > EPS and not seen[e.to]:
                    seen[e.to] = True
                    q.append(e.to)
        return seen

    def reachable_from_source(self, s: int) -> list[bool]:
        """最大流计算后，从源点残余可达的节点集合（最小源侧割）。"""
        return self.reachable_set(s)

    def reachable_to_sink(self, t: int) -> list[bool]:
        """最大流计算后，能沿残余边到达汇点 t 的节点集合（反向 BFS）。"""
        seen = [False] * self.n
        seen[t] = True
        q = deque([t])
        while q:
            u = q.popleft()
            for e in self.g[u]:
                # e 为 u→e.to 的正向边，其反向边 e.to→u 若有余量，
                # 则 e.to 在残余网络中可达 u，进而可达 t。
                back = self.g[e.to][e.rev]
                if back.cap > EPS and not seen[e.to]:
                    seen[e.to] = True
                    q.append(e.to)
        return seen


def _clean_name(raw, field: str) -> str:
    if not isinstance(raw, str):
        raise NetworkValidationError(f"{field} 必须是字符串", field)
    name = raw.strip()
    if not name:
        raise NetworkValidationError(f"{field} 不能为空", field)
    return name


def _finite_positive_number(raw, field: str) -> float:
    import math

    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise NetworkValidationError(f"{field} 必须是正数", field)
    value = float(raw)
    if not math.isfinite(value):
        raise NetworkValidationError(f"{field} 必须是有限数值", field)
    if value <= 0:
        raise NetworkValidationError(f"{field} 必须大于 0", field)
    return value


def _prepare(*, source, sink, nodes, edges, required_flow) -> dict:
    """业务校验 + 规范化，供审计与复核共用（复核即从完整草稿重新执行审计）。

    ``nodes`` 为汇合节点（及其它中间节点）列表；泄压源与安全焚烧端
    自动并入节点集合。``edges`` 每项形如::

        {"id": "E1" | None, "from": "S", "to": "T",
         "capacity": 100.0, "maintainable": True}
    """
    import math

    source = _clean_name(source, "泄压源")
    sink = _clean_name(sink, "安全焚烧端")
    if source == sink:
        raise NetworkValidationError("泄压源与安全焚烧端不能是同一节点", "sink")

    if isinstance(required_flow, bool) or not isinstance(required_flow, (int, float)):
        raise NetworkValidationError("事故持续排出流量必须是正数", "required_flow")
    required_flow = float(required_flow)
    if not math.isfinite(required_flow) or required_flow <= 0:
        raise NetworkValidationError("事故持续排出流量必须是大于 0 的有限数值", "required_flow")

    if not isinstance(nodes, list):
        raise NetworkValidationError("汇合节点必须是列表", "nodes")

    node_set: set[str] = {source, sink}
    junction_names: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(nodes):
        field = f"nodes[{i}]"
        name = _clean_name(raw, field)
        if name in seen:
            raise NetworkValidationError(f"汇合节点“{name}”重复", field)
        seen.add(name)
        junction_names.append(name)
        node_set.add(name)

    if not isinstance(edges, list):
        raise NetworkValidationError("管段必须是列表", "edges")

    clean_edges: list[dict] = []
    for i, raw in enumerate(edges):
        if not isinstance(raw, dict):
            raise NetworkValidationError(f"第 {i + 1} 条管段格式无效", f"edges[{i}]")
        label = raw.get("id")
        if label is not None and not (isinstance(label, str) and label.strip()):
            label = None
        elif isinstance(label, str):
            label = label.strip()

        u = _clean_name(raw.get("from"), f"第 {i + 1} 条管段起点")
        v = _clean_name(raw.get("to"), f"第 {i + 1} 条管段终点")
        if u not in node_set:
            raise NetworkValidationError(
                f"第 {i + 1} 条管段起点“{u}”未在节点中定义", f"edges[{i}].from"
            )
        if v not in node_set:
            raise NetworkValidationError(
                f"第 {i + 1} 条管段终点“{v}”未在节点中定义", f"edges[{i}].to"
            )
        if u == v:
            raise NetworkValidationError(
                f"第 {i + 1} 条管段起点和终点不能相同（{u}）", f"edges[{i}].to"
            )
        cap = _finite_positive_number(raw.get("capacity"), f"第 {i + 1} 条管段最大流量")
        maintainable = bool(raw.get("maintainable", False))
        clean_edges.append(
            {
                "index": i,
                "position": i + 1,
                "id": label,
                "from": u,
                "to": v,
                "capacity": cap,
                "maintainable": maintainable,
            }
        )

    all_nodes = sorted(node_set)
    index_of = {name: i for i, name in enumerate(all_nodes)}
    return {
        "source": source,
        "sink": sink,
        "required_flow": required_flow,
        "edges": clean_edges,
        "all_nodes": all_nodes,
        "index_of": index_of,
    }


def _solve(prep: dict, removed_index: Optional[int]) -> tuple[Dinic, float, dict, list[dict]]:
    """在一张**全新**的网络上独立求最大流，返回残余网络、流量与最小割证据。"""
    index_of = prep["index_of"]
    all_nodes = prep["all_nodes"]
    dinic = Dinic(len(all_nodes))
    active = []
    for e in prep["edges"]:
        if e["index"] == removed_index:
            continue
        dinic.add_edge(index_of[e["from"]], index_of[e["to"]], e["capacity"])
        active.append(e)
    s = index_of[prep["source"]]
    t = index_of[prep["sink"]]
    value = dinic.max_flow(s, t)
    side = dinic.reachable_from_source(s)

    source_side = sorted(all_nodes[k] for k, ok in enumerate(side) if ok)
    sink_side = sorted(all_nodes[k] for k, ok in enumerate(side) if not ok)
    cut_edges = []
    cut_capacity = 0.0
    for e in active:  # 按录入顺序列出，便于复核
        if side[index_of[e["from"]]] and not side[index_of[e["to"]]]:
            cut_edges.append(
                {
                    "index": e["index"],
                    "position": e["position"],
                    "id": e["id"],
                    "from": e["from"],
                    "to": e["to"],
                    "capacity": _num(e["capacity"]),
                }
            )
            cut_capacity += e["capacity"]
    cut = {
        "capacity": _num(cut_capacity),
        "source_side_nodes": source_side,
        "sink_side_nodes": sink_side,
        "cut_edges": cut_edges,
    }
    return dinic, value, cut, active


def _solve_all(prep: dict) -> list[dict]:
    """正常网络 + 每条可检修管段单独失效，各自在全新网络上独立求解。"""
    runs = []
    dinic, value, cut, active = _solve(prep, None)
    runs.append(
        {"stage": "normal", "removed": None, "dinic": dinic,
         "value": value, "cut": cut, "active": active}
    )
    for e in prep["edges"]:
        if not e["maintainable"]:
            continue
        dinic, value, cut, active = _solve(prep, e["index"])
        runs.append(
            {"stage": "single_failure", "removed": e, "dinic": dinic,
             "value": value, "cut": cut, "active": active}
        )
    return runs


def _build_audit(prep: dict, runs: list[dict]) -> dict:
    """由各情形独立求解结果组装既有审计结论（结构即 ``POST /api/audit`` 响应）。"""
    required_flow = prep["required_flow"]

    def _meets(value: float) -> bool:
        return value + EPS >= required_flow

    normal = runs[0]

    scenarios = []
    failure = None
    for run in runs[1:]:
        e = run["removed"]
        scenario = {
            "edge_index": e["index"],
            "position": e["position"],
            "edge_id": e["id"],
            "from": e["from"],
            "to": e["to"],
            "capacity": _num(e["capacity"]),
            "max_flow": _num(run["value"]),
            "meets": _meets(run["value"]),
        }
        scenarios.append(scenario)
        # 按管段录入顺序取首条不达标者
        if failure is None and not _meets(run["value"]):
            failure = {
                "stage": "single_failure",
                "edge_index": e["index"],
                "position": e["position"],
                "edge_id": e["id"],
                "from": e["from"],
                "to": e["to"],
                "capacity": _num(e["capacity"]),
                "required_flow": _num(required_flow),
                "max_flow": _num(run["value"]),
                "cut": run["cut"],
            }

    # 没有任何可检修管段时，至少正常网络本身必须达标
    if failure is None and not scenarios and not _meets(normal["value"]):
        failure = {
            "stage": "normal",
            "edge_index": None,
            "position": None,
            "edge_id": None,
            "from": None,
            "to": None,
            "capacity": None,
            "required_flow": _num(required_flow),
            "max_flow": _num(normal["value"]),
            "cut": normal["cut"],
        }

    return {
        "passed": failure is None and _meets(normal["value"]),
        "required_flow": _num(required_flow),
        "normal": {
            "max_flow": _num(normal["value"]),
            "meets": _meets(normal["value"]),
            "cut": normal["cut"],
        },
        "scenarios": scenarios,
        "failure": failure,
    }


def audit_network(
    *,
    source: str,
    sink: str,
    nodes: list[str],
    edges: list[dict],
    required_flow: float,
) -> dict:
    """校验输入并执行正常网络 + 全部单点失效情景的最大流审计。

    返回可直接 JSON 序列化的审计结论（见模块 docstring 与 README）。
    """
    prep = _prepare(source=source, sink=sink, nodes=nodes,
                    edges=edges, required_flow=required_flow)
    return _build_audit(prep, _solve_all(prep))


def _classify_min_cut_membership(prep: dict, run: dict) -> list[dict]:
    """判定该情形每条活跃管段在**全部**源侧最小割中的归属。

    最大流算完后在残余网络上令：

    * ``R`` = 从源残余可达的节点集（即最小的源侧最小割）；
    * ``W`` = 能沿残余边到达汇的节点集（``V∖W`` 即最大的源侧最小割）。

    任一源侧最小割 ``C`` 满足 ``R ⊆ C ⊆ V∖W``，且 ``C`` 沿残余边
    “出封闭”（``C`` 内节点经残余边可达的节点仍在 ``C`` 内）。
    于是对管段 ``u → v``：

    * **全部**最小割均跨越  ⟺  ``u ∈ R`` 且 ``v ∈ W``
      （每个最小割的源侧必含 ``R``、必不含 ``W`` 中节点）；
    * **存在**最小割跨越    ⟺  ``v ∉ R``、``u ∉ W``，且残余网络中
      ``u`` 不可达 ``v``（充分性：取 ``C = R ∪ reach(u)`` 即为一个
      跨越它的最小割；必要性：出封闭的 ``C`` 若含 ``u`` 必含
      ``reach(u)``，三条件缺一不可）；
    * 否则**从不**跨割——特别地，未满载管段必然从不跨割：跨越未
      满载边的割容量严格大于最大流，绝不是最小割。

    最小割的数量可能随网络规模指数增长，以上判定在残余网络上直接
    完成，等价于枚举全部最小割后的归属统计。
    """
    dinic = run["dinic"]
    index_of = prep["index_of"]
    s = index_of[prep["source"]]
    t = index_of[prep["sink"]]
    src_reach = dinic.reachable_from_source(s)   # R
    sink_reach = dinic.reachable_to_sink(t)      # W
    # 本次最大流运行恰好返回的（R, V∖R）割集，用于对照“偶然割集管段”
    in_returned = {e["index"] for e in run["cut"]["cut_edges"]}

    reach_cache: dict[int, list[bool]] = {}

    def _reach(u: int) -> list[bool]:
        if u not in reach_cache:
            reach_cache[u] = dinic.reachable_set(u)
        return reach_cache[u]

    graded = []
    for e in run["active"]:  # 按录入顺序列出，便于复核
        u = index_of[e["from"]]
        v = index_of[e["to"]]
        if src_reach[u] and sink_reach[v]:
            crosses = "all"
        elif (not src_reach[v]) and (not sink_reach[u]) and (not _reach(u)[v]):
            crosses = "some"
        else:
            crosses = "never"
        graded.append(
            {
                "edge_index": e["index"],
                "position": e["position"],
                "edge_id": e["id"],
                "from": e["from"],
                "to": e["to"],
                "capacity": _num(e["capacity"]),
                "crosses": crosses,
                "in_returned_cut": e["index"] in in_returned,
            }
        )
    return graded


def review_network(
    *,
    source: str,
    sink: str,
    nodes: list[str],
    edges: list[dict],
    required_flow: float,
) -> dict:
    """薄弱管段复核：从完整草稿**重新执行既有审计**，再对每个达标情形
    给出全部源侧最小割的管段归属分级与导排裕量。

    * 任一情形本已不达标（审计不通过）：保留既有首条失败证据
      （``failure`` 与 ``POST /api/audit`` 完全一致），且**不生成**
      任何薄弱分级（各情形 ``classified=False``、``edges=None``）；
    * 全部情形达标：逐情形返回最小割容量（＝该情形最大可导排量）、
      相对事故要求流量的裕量，以及每条管段的三档归属
      （``never`` 从不跨割 / ``some`` 仅部分最小割跨越 /
      ``all`` 全部最小割均跨越），并标记该管段是否恰好落在本次
      最大流运行返回的单个割集中，以便工程师区分“单次割集中的
      偶然管段”与“所有同容量瓶颈均不可绕开的管段”。
    """
    prep = _prepare(source=source, sink=sink, nodes=nodes,
                    edges=edges, required_flow=required_flow)
    runs = _solve_all(prep)
    audit = _build_audit(prep, runs)
    required_flow = prep["required_flow"]

    def _meets(value: float) -> bool:
        return value + EPS >= required_flow

    scenarios = []
    for run in runs:
        removed = run["removed"]
        entry = {
            "stage": run["stage"],
            "removed": None if removed is None else {
                "edge_index": removed["index"],
                "position": removed["position"],
                "edge_id": removed["id"],
                "from": removed["from"],
                "to": removed["to"],
                "capacity": _num(removed["capacity"]),
            },
            "max_flow": _num(run["value"]),
            "meets": _meets(run["value"]),
        }
        if audit["passed"]:
            # 仅对达标情形生成薄弱分级
            graded = _classify_min_cut_membership(prep, run)
            entry.update(
                {
                    "min_cut_capacity": run["cut"]["capacity"],
                    "margin": _num(run["cut"]["capacity"] - required_flow),
                    "classified": True,
                    "edges": graded,
                    "summary": {
                        "all": sum(1 for g in graded if g["crosses"] == "all"),
                        "some": sum(1 for g in graded if g["crosses"] == "some"),
                        "never": sum(1 for g in graded if g["crosses"] == "never"),
                    },
                }
            )
        else:
            # 情形本已不达标：只保留审计数值，不生成薄弱分级
            entry.update(
                {
                    "min_cut_capacity": None,
                    "margin": None,
                    "classified": False,
                    "edges": None,
                    "summary": None,
                }
            )
        scenarios.append(entry)

    return {
        "passed": audit["passed"],
        "required_flow": audit["required_flow"],
        "scenarios": scenarios,
        "failure": audit["failure"],
    }


def _num(x: float) -> float:
    """消除浮点尾差，便于展示与复核（如 0.30000000000000004）。"""
    r = round(float(x), 6)
    return 0.0 if r == 0 else r
