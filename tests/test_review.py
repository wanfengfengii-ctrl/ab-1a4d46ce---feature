"""薄弱管段复核：全部最小割视角的管段分级测试。

关键性质：分级结果必须与“枚举全部源侧最小割后统计每条管段跨越情况”
的暴力结果完全一致——复核的意义正是区分单次割集中的偶然管段与所有
同容量瓶颈中均不可绕开的管段。
"""
import itertools
import random

import pytest

from app.flow import NetworkValidationError, audit_network, review_network


def _parallel_edges():
    """两条 100 干线并联：S→A→T 与 S→B→T。"""
    return [
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": True},
        {"id": "E3", "from": "S", "to": "B", "capacity": 100, "maintainable": True},
        {"id": "E4", "from": "B", "to": "T", "capacity": 100, "maintainable": True},
    ]


def _brute_force_classes(source, sink, junctions, edges, removed_position=None):
    """枚举全部源侧子集，找出容量最小的割集，统计每条管段跨越最小割的情况。

    返回 (最小割容量, {position: "all" | "some" | "never"})。
    """
    active = [e for e in edges if e.get("position") != removed_position]
    cuts = []
    for r in range(len(junctions) + 1):
        for combo in itertools.combinations(junctions, r):
            side = {source, *combo}
            cap = sum(
                e["capacity"] for e in active
                if e["from"] in side and e["to"] not in side
            )
            cuts.append((cap, frozenset(side)))
    min_cap = min(c for c, _ in cuts)
    min_cuts = [side for c, side in cuts if abs(c - min_cap) < 1e-9]
    classes = {}
    for e in active:
        crosses = [e["from"] in side and e["to"] not in side for side in min_cuts]
        if all(crosses):
            classes[e["position"]] = "all"
        elif any(crosses):
            classes[e["position"]] = "some"
        else:
            classes[e["position"]] = "never"
    return min_cap, classes


def _numbered(edges):
    return [dict(e, position=i + 1) for i, e in enumerate(edges)]


def test_unique_bottleneck_classified_all():
    """唯一瓶颈：A→T 被全部最小割跨越；上游宽裕的 S→A 从不跨割。"""
    edges = [
        {"id": "E1", "from": "S", "to": "A", "capacity": 200, "maintainable": False},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": False},
    ]
    r = review_network(source="S", sink="T", nodes=["A"], edges=edges, required_flow=100)
    assert r["reviewable"] is True
    assert r["failure"] is None
    (normal,) = r["scenarios"]
    assert normal["stage"] == "normal"
    assert normal["removed"] is None
    by_id = {e["id"]: e["class"] for e in normal["edges"]}
    assert by_id == {"E1": "never", "E2": "all"}
    assert normal["class_counts"] == {"all": 1, "some": 0, "never": 1}
    # 最小割容量 == 最大可导排量；裕量 = 最大可导排量 − 事故要求
    assert normal["min_cut_capacity"] == normal["max_flow"] == 100
    assert normal["margin"] == 0
    # 唯一最小割：源侧交集 == 源侧并集
    assert normal["min_source_side_nodes"] == ["A", "S"]
    assert normal["max_source_side_nodes"] == ["A", "S"]


def test_parallel_paths_no_edge_in_every_cut():
    """两条并联干线：单次最大流只返回一个割集，但没有任何管段被全部最小割跨越。"""
    r = review_network(source="S", sink="T", nodes=["A", "B"],
                       edges=_parallel_edges(), required_flow=95)
    assert r["reviewable"] is True
    normal = r["scenarios"][0]
    assert normal["max_flow"] == 200
    assert normal["margin"] == 105
    # 4 个等容量最小割（{S}、{S,A}、{S,B}、{S,A,B}），每条管段只被部分跨越
    assert all(e["class"] == "some" for e in normal["edges"])
    assert normal["class_counts"] == {"all": 0, "some": 4, "never": 0}
    assert normal["min_source_side_nodes"] == ["S"]
    assert normal["max_source_side_nodes"] == ["A", "B", "S"]


def test_single_failure_scenario_classification():
    """失效情形在残余网络上独立分级：失效管段排除在外，闲置管段与瓶颈无关。"""
    r = review_network(source="S", sink="T", nodes=["A", "B"],
                       edges=_parallel_edges(), required_flow=95)
    assert len(r["scenarios"]) == 5  # 正常网络 + 4 条可检修管段
    scen = r["scenarios"][1]
    assert scen["stage"] == "single_failure"
    assert scen["removed"]["edge_id"] == "E1"
    assert scen["removed"]["position"] == 1
    # 失效管段不参与分级
    assert all(e["id"] != "E1" for e in scen["edges"])
    by_id = {e["id"]: e["class"] for e in scen["edges"]}
    # E1 失效后仅剩 S→B→T 干线：E2 无流量与瓶颈无关，E3/E4 各自只被部分最小割跨越
    assert by_id == {"E2": "never", "E3": "some", "E4": "some"}
    assert scen["max_flow"] == scen["min_cut_capacity"] == 100
    assert scen["margin"] == 5


def test_failing_draft_not_reviewable_keeps_first_failure_evidence():
    """情形本已不达标：保留既有首条失败证据，不生成薄弱分级。"""
    edges = [
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": True},
        {"id": "E3", "from": "S", "to": "B", "capacity": 90, "maintainable": True},
        {"id": "E4", "from": "B", "to": "T", "capacity": 90, "maintainable": True},
    ]
    kwargs = dict(source="S", sink="T", nodes=["A", "B"], edges=edges, required_flow=95)
    r = review_network(**kwargs)
    assert r["reviewable"] is False
    assert r["passed"] is False
    assert r["scenarios"] is None  # 不得生成薄弱分级
    f = r["failure"]
    assert f["stage"] == "single_failure"
    assert f["position"] == 1 and f["edge_id"] == "E1"
    assert f["cut"]["capacity"] == f["max_flow"] == 90
    # 与既有审计的首条失败证据完全一致
    assert f == audit_network(**kwargs)["failure"]


def test_normal_stage_failure_also_not_reviewable():
    """无可检修管段且正常网络不达标：失败证据为 normal 阶段，同样不生成分级。"""
    edges = [{"id": "E1", "from": "S", "to": "T", "capacity": 50, "maintainable": False}]
    r = review_network(source="S", sink="T", nodes=[], edges=edges, required_flow=95)
    assert r["reviewable"] is False
    assert r["scenarios"] is None
    assert r["failure"]["stage"] == "normal"
    assert r["failure"]["cut"]["capacity"] == 50


def test_review_no_maintainable_edges_covers_normal_only():
    edges = [
        {"id": "E1", "from": "S", "to": "T", "capacity": 100, "maintainable": False},
    ]
    r = review_network(source="S", sink="T", nodes=[], edges=edges, required_flow=50)
    assert r["reviewable"] is True
    assert len(r["scenarios"]) == 1
    assert r["scenarios"][0]["stage"] == "normal"
    assert r["scenarios"][0]["edges"][0]["class"] == "all"


def test_review_reuses_audit_validation():
    with pytest.raises(NetworkValidationError):
        review_network(source="S", sink="S", nodes=[], edges=[], required_flow=1)
    with pytest.raises(NetworkValidationError):
        review_network(source="S", sink="T", nodes=[],
                       edges=[{"from": "S", "to": "X", "capacity": 1}], required_flow=1)


def test_review_matches_brute_force_all_min_cuts():
    """随机小网络：复核分级与暴力枚举全部最小割的结果完全一致。"""
    rng = random.Random(20260926)
    checked_normal = checked_failure = 0
    for _ in range(200):
        junctions = [f"N{i}" for i in range(rng.randint(0, 4))]
        names = ["S", "T"] + junctions
        edges = []
        # 保底一条 S→…→T 链，让正常网络大概率达标
        chain = ["S"] + rng.sample(junctions, k=rng.randint(0, len(junctions))) + ["T"]
        for u, v in zip(chain, chain[1:]):
            edges.append({
                "from": u,
                "to": v,
                "capacity": rng.randint(1, 9),
                "maintainable": rng.random() < 0.5,
            })
        for _ in range(rng.randint(0, 8)):
            u = rng.choice(names)
            v = rng.choice(names)
            if u == v:
                continue
            edges.append({
                "from": u,
                "to": v,
                "capacity": rng.randint(1, 9),
                "maintainable": rng.random() < 0.5,
            })
        kwargs = dict(source="S", sink="T", nodes=junctions, edges=edges, required_flow=0.5)
        res = review_network(**kwargs)
        numbered = _numbered(edges)
        if not res["reviewable"]:
            continue  # 本已不达标的情形不生成分级，已由专门测试覆盖
        for scen in res["scenarios"]:
            removed = scen["removed"]["position"] if scen["removed"] else None
            min_cap, expected = _brute_force_classes("S", "T", junctions, numbered, removed)
            # 最小割容量 == 该情形最大可导排量
            assert scen["min_cut_capacity"] == pytest.approx(min_cap)
            assert scen["max_flow"] == pytest.approx(min_cap)
            actual = {e["position"]: e["class"] for e in scen["edges"]}
            assert actual == expected
            if scen["stage"] == "normal":
                checked_normal += 1
            else:
                checked_failure += 1
    # 确保性质测试确实覆盖了足够多的正常与失效情形
    assert checked_normal >= 20
    assert checked_failure >= 20
