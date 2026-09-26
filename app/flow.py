"""最大流 / 最小割引擎、检修审计与薄弱管段复核逻辑。

规则（对应业务要求）：

* 每条管段被视作一条有向边，录入的最大流量即其容量上限；
* 在**正常网络**与**每一条可检修管段单独移除后的残余网络**上，
  分别独立运行最大流（Dinic），互不复用中间流量结果；
* 事故要求的持续排出流量必须在正常网络和每一个单点失效情景下都可达，
  审计才放行；
* 失效时按管段录入顺序返回第一条不达标管段，并依据最大流 / 最小割定理，
  从残余网络给出可复核的源侧割集、焚烧端侧节点及割集容量。

薄弱管段复核（审计通过后方可发起）：

* 复核从完整草稿**重新执行**上述既有审计；任一情形本已不达标时，
  保留既有首条失败证据，且不生成任何薄弱分级；
* 全部情形达标时，对每个情形考察**全部**容量等于该情形最大可导排量的
  源侧最小割（而非单次最大流恰好返回的那一个割集），把每条管段分为：
  - ``all``   全部最小割均跨越：任何同容量瓶颈都绕不开，真正限制导排余量；
  - ``some``  仅部分最小割跨越：可替代瓶颈，单次割集可能偶然包含它；
  - ``never`` 从不跨割：与瓶颈无关。
* 分类不枚举指数级最小割，而是在残余网络上用 Picard–Queyranne 结构
  精确判定：源可达集 R_s 是所有最小割源侧的交集、可反向到汇的集合 R_t
  是所有最小割焚烧端侧的交集；边 (u,v) 跨全部最小割 ⟺ u∈R_s 且 v∈R_t；
  跨部分最小割 ⟺ 已饱和且 u、v 不在残余网络同一强连通分量。

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

    def add_edge(self, u: int, v: int, cap: float) -> _Edge:
        """添加有向边，返回正向边对象（事后可读取其残余容量）。"""
        fwd = _Edge(to=v, rev=len(self.g[v]), cap=float(cap))
        bak = _Edge(to=u, rev=len(self.g[u]), cap=0.0)
        self.g[u].append(fwd)
        self.g[v].append(bak)
        return fwd

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

    def _residual_arcs(self):
        """最大流计算后，残余网络中容量 > 0 的有向弧。"""
        for u in range(self.n):
            for e in self.g[u]:
                if e.cap > EPS:
                    yield u, e.to

    def reachable_from_source(self, s: int) -> list[bool]:
        """最大流计算后，沿残余容量 > 0 的边做 BFS，得到源侧节点集合。

        该集合是**所有**源侧最小割的交集（最小的源侧）。
        """
        seen = [False] * self.n
        seen[s] = True
        q = deque([s])
        while q:
            u = q.popleft()
            for e in self.g[u]:
                if e.cap > EPS and not seen[e.to]:
                    seen[e.to] = True
                    q.append(e.to)
        return seen

    def reachable_to_sink(self, t: int) -> list[bool]:
        """最大流计算后，在残余网络中能到达汇点的节点集合（反向 BFS）。

        该集合是**所有**最小割焚烧端侧的交集（最小的汇侧）。
        """
        radj: list[list[int]] = [[] for _ in range(self.n)]
        for u, v in self._residual_arcs():
            radj[v].append(u)
        seen = [False] * self.n
        seen[t] = True
        q = deque([t])
        while q:
            u = q.popleft()
            for a in radj[u]:
                if not seen[a]:
                    seen[a] = True
                    q.append(a)
        return seen

    def scc_ids(self) -> list[int]:
        """残余网络的强连通分量编号（迭代版 Tarjan）。

        同一分量内的节点在任何源侧最小割中必然同侧；最小割与分量 DAG 上
        “含源分量、不含汇分量的闭集”一一对应（Picard–Queyranne）。
        """
        adj: list[list[int]] = [[] for _ in range(self.n)]
        for u, v in self._residual_arcs():
            adj[u].append(v)

        index = [-1] * self.n
        low = [0] * self.n
        on_stack = [False] * self.n
        stack: list[int] = []
        comp = [-1] * self.n
        ncomp = 0
        counter = 0

        for root in range(self.n):
            if index[root] >= 0:
                continue
            work = [(root, 0)]
            while work:
                u, i = work[-1]
                if i == 0:
                    index[u] = low[u] = counter
                    counter += 1
                    stack.append(u)
                    on_stack[u] = True
                if i < len(adj[u]):
                    work[-1] = (u, i + 1)
                    w = adj[u][i]
                    if index[w] < 0:
                        work.append((w, 0))
                    elif on_stack[w]:
                        low[u] = min(low[u], index[w])
                else:
                    work.pop()
                    if low[u] == index[u]:
                        while True:
                            w = stack.pop()
                            on_stack[w] = False
                            comp[w] = ncomp
                            if w == u:
                                break
                        ncomp += 1
                    if work:
                        p = work[-1][0]
                        low[p] = min(low[p], low[u])
        return comp


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


def _prepare_network(
    *,
    source: str,
    sink: str,
    nodes: list[str],
    edges: list[dict],
    required_flow: float,
) -> dict:
    """业务校验并整理出可求解的网络（审计与复核共用同一份草稿口径）。"""
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


def _solve_scenario(prep: dict, removed_index: Optional[int]) -> dict:
    """在一张**全新**的网络上独立求最大流，返回流量、最小割证据与残余网络。"""
    all_nodes = prep["all_nodes"]
    index_of = prep["index_of"]
    dinic = Dinic(len(all_nodes))
    active: list[dict] = []
    residuals: list[_Edge] = []
    for e in prep["edges"]:
        if e["index"] == removed_index:
            continue
        fwd = dinic.add_edge(index_of[e["from"]], index_of[e["to"]], e["capacity"])
        active.append(e)
        residuals.append(fwd)
    value = dinic.max_flow(index_of[prep["source"]], index_of[prep["sink"]])
    side = dinic.reachable_from_source(index_of[prep["source"]])

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
    return {
        "value": value,
        "cut": {
            "capacity": _num(cut_capacity),
            "source_side_nodes": source_side,
            "sink_side_nodes": sink_side,
            "cut_edges": cut_edges,
        },
        "dinic": dinic,
        "active": active,
        "residuals": residuals,
    }


def _run_audit(prep: dict) -> tuple[dict, dict]:
    """执行既有审计：正常网络 + 每条可检修管段单独失效。

    返回 ``(审计结论, 各情形解)``；各情形解保留残余网络，供薄弱复核
    在不重跑最大流的情况下做全最小割分类。
    """
    required_flow = prep["required_flow"]

    def _meets(value: float) -> bool:
        return value + EPS >= required_flow

    solutions: dict = {}

    # 1) 正常网络
    sol_normal = _solve_scenario(prep, None)
    solutions[None] = sol_normal
    normal_value = sol_normal["value"]

    # 2) 每条可检修管段单独临时失效（残余网络独立求解）
    scenarios = []
    failure = None
    for e in prep["edges"]:
        if not e["maintainable"]:
            continue
        sol = _solve_scenario(prep, e["index"])
        solutions[e["index"]] = sol
        value = sol["value"]
        scenario = {
            "edge_index": e["index"],
            "position": e["position"],
            "edge_id": e["id"],
            "from": e["from"],
            "to": e["to"],
            "capacity": _num(e["capacity"]),
            "max_flow": _num(value),
            "meets": _meets(value),
        }
        scenarios.append(scenario)
        # 按管段录入顺序取首条不达标者
        if failure is None and not _meets(value):
            failure = {
                "stage": "single_failure",
                "edge_index": e["index"],
                "position": e["position"],
                "edge_id": e["id"],
                "from": e["from"],
                "to": e["to"],
                "capacity": _num(e["capacity"]),
                "required_flow": _num(required_flow),
                "max_flow": _num(value),
                "cut": sol["cut"],
            }

    # 没有任何可检修管段时，至少正常网络本身必须达标
    if failure is None and not scenarios and not _meets(normal_value):
        failure = {
            "stage": "normal",
            "edge_index": None,
            "position": None,
            "edge_id": None,
            "from": None,
            "to": None,
            "capacity": None,
            "required_flow": _num(required_flow),
            "max_flow": _num(normal_value),
            "cut": sol_normal["cut"],
        }

    result = {
        "passed": failure is None and _meets(normal_value),
        "required_flow": _num(required_flow),
        "normal": {
            "max_flow": _num(normal_value),
            "meets": _meets(normal_value),
            "cut": sol_normal["cut"],
        },
        "scenarios": scenarios,
        "failure": failure,
    }
    return result, solutions


def audit_network(
    *,
    source: str,
    sink: str,
    nodes: list[str],
    edges: list[dict],
    required_flow: float,
) -> dict:
    """校验输入并执行正常网络 + 全部单点失效情景的最大流审计。

    ``nodes`` 为汇合节点（及其它中间节点）列表；泄压源与安全焚烧端
    自动并入节点集合。``edges`` 每项形如::

        {"id": "E1" | None, "from": "S", "to": "T",
         "capacity": 100.0, "maintainable": True}

    返回可直接 JSON 序列化的审计结论（见模块 docstring 与 README）。
    """
    prep = _prepare_network(
        source=source, sink=sink, nodes=nodes, edges=edges, required_flow=required_flow
    )
    result, _ = _run_audit(prep)
    return result


def _review_scenario(prep: dict, sol: dict, *, stage: str, removed: Optional[dict]) -> dict:
    """对一个达标情形做全最小割分类（基于已求出的残余网络，不重跑最大流）。"""
    dinic = sol["dinic"]
    all_nodes = prep["all_nodes"]
    index_of = prep["index_of"]
    s = index_of[prep["source"]]
    t = index_of[prep["sink"]]

    reach_s = dinic.reachable_from_source(s)  # 所有最小割源侧的交集
    reach_t = dinic.reachable_to_sink(t)      # 所有最小割焚烧端侧的交集
    comp = dinic.scc_ids()                    # 残余网络强连通分量

    classified = []
    counts = {"all": 0, "some": 0, "never": 0}
    for e, fwd in zip(sol["active"], sol["residuals"]):
        u = index_of[e["from"]]
        v = index_of[e["to"]]
        if reach_s[u] and reach_t[v]:
            # u 在每个最小割的源侧、v 在每个最小割的汇侧 → 任何同容量瓶颈都绕不开
            cls = "all"
        elif fwd.cap <= EPS and comp[u] != comp[v]:
            # 已饱和且不被残余环锁在同侧 → 存在跨过它的最小割，也存在避开它的
            cls = "some"
        else:
            cls = "never"
        counts[cls] += 1
        classified.append(
            {
                "index": e["index"],
                "position": e["position"],
                "id": e["id"],
                "from": e["from"],
                "to": e["to"],
                "capacity": _num(e["capacity"]),
                "class": cls,
            }
        )

    min_source_side = sorted(all_nodes[k] for k, ok in enumerate(reach_s) if ok)
    max_source_side = sorted(all_nodes[k] for k, ok in enumerate(reach_t) if not ok)
    value = sol["value"]
    out = {
        "stage": stage,
        "removed": None,
        "max_flow": _num(value),
        "min_cut_capacity": sol["cut"]["capacity"],
        "required_flow": _num(prep["required_flow"]),
        "margin": _num(value - prep["required_flow"]),
        "meets": True,
        "min_source_side_nodes": min_source_side,
        "max_source_side_nodes": max_source_side,
        "edges": classified,
        "class_counts": counts,
    }
    if removed is not None:
        out["removed"] = {
            "index": removed["index"],
            "position": removed["position"],
            "edge_id": removed["id"],
            "from": removed["from"],
            "to": removed["to"],
            "capacity": _num(removed["capacity"]),
        }
    return out


def review_network(
    *,
    source: str,
    sink: str,
    nodes: list[str],
    edges: list[dict],
    required_flow: float,
) -> dict:
    """薄弱管段复核：从完整草稿**重新执行**既有审计，再做全最小割分类。

    * 任一情形本已不达标（审计未通过）：保留既有首条失败证据，
      ``scenarios`` 为 ``None``，不生成任何薄弱分级；
    * 全部情形达标：对每个情形（正常网络 + 每条可检修管段单独失效）
      返回最小割容量、相对事故要求流量的裕量，以及每条管段相对
      **全部**等容量源侧最小割的归属（``all`` / ``some`` / ``never``）。
    """
    prep = _prepare_network(
        source=source, sink=sink, nodes=nodes, edges=edges, required_flow=required_flow
    )
    audit, solutions = _run_audit(prep)

    if not audit["passed"]:
        return {
            "reviewable": False,
            "passed": False,
            "required_flow": audit["required_flow"],
            "failure": audit["failure"],
            "scenarios": None,
        }

    reviews = [_review_scenario(prep, solutions[None], stage="normal", removed=None)]
    for e in prep["edges"]:
        if not e["maintainable"]:
            continue
        reviews.append(
            _review_scenario(prep, solutions[e["index"]], stage="single_failure", removed=e)
        )

    return {
        "reviewable": True,
        "passed": True,
        "required_flow": audit["required_flow"],
        "failure": None,
        "scenarios": reviews,
    }


def _num(x: float) -> float:
    """消除浮点尾差，便于展示与复核（如 0.30000000000000004）。"""
    r = round(float(x), 6)
    return 0.0 if r == 0 else r
