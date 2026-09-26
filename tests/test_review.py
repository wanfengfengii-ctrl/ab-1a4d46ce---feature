"""薄弱管段复核：全部最小割归属分级与导排裕量。

重点验证业务约束：
* 复核从完整草稿重新执行既有审计（同样的校验、同样的情形数值）；
* 只对达标情形生成薄弱分级；任一情形不达标时保留既有首条失败证据，
  且不生成任何分级；
* 三档归属正确：never（从不跨割）/ some（仅部分最小割跨越）/
  all（全部最小割均跨越）——而非只凭一次最大流恰好返回的割集；
* 裕量 = 最小割容量 − 事故要求流量，最小割容量 = 该情形最大可导排量。
"""
import pytest

from app.flow import NetworkValidationError, audit_network, review_network


def _grades(scenario):
    return {g["edge_id"]: g["crosses"] for g in scenario["edges"]}


def test_series_bottleneck_crosses_all_min_cuts():
    """串联瓶颈：S→A(50) 是唯一最小割，A→T(100) 未满载从不跨割。"""
    r = review_network(source="S", sink="T", nodes=["A"], required_flow=1, edges=[
        {"id": "E1", "from": "S", "to": "A", "capacity": 50, "maintainable": False},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": False},
    ])
    assert r["passed"] is True
    (normal,) = r["scenarios"]
    assert normal["stage"] == "normal"
    assert normal["min_cut_capacity"] == 50 == normal["max_flow"]
    assert normal["margin"] == 49                      # 50 − 1
    assert _grades(normal) == {"E1": "all", "E2": "never"}
    assert normal["summary"] == {"all": 1, "some": 0, "never": 1}


def test_parallel_trunks_some_not_all_and_returned_cut_is_accidental():
    """两条并联干线：单次运行恰好返回割 {E1,E3}，但四条管段都只是
    部分最小割跨越——没有任何一条在所有同容量瓶颈中不可绕开。"""
    r = review_network(source="S", sink="T", nodes=["A", "B"], required_flow=95, edges=[
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": True},
        {"id": "E3", "from": "S", "to": "B", "capacity": 100, "maintainable": True},
        {"id": "E4", "from": "B", "to": "T", "capacity": 100, "maintainable": True},
    ])
    assert r["passed"] is True
    normal = r["scenarios"][0]
    assert _grades(normal) == {"E1": "some", "E2": "some", "E3": "some", "E4": "some"}
    # 本次最大流恰好返回的割集只含 E1/E3——它们正是“偶然割集管段”
    in_cut = {g["edge_id"]: g["in_returned_cut"] for g in normal["edges"]}
    assert in_cut == {"E1": True, "E2": False, "E3": True, "E4": False}
    # 失效情形同样不得把单条干线误判为必经（每条失效后仍剩 100）
    for sc in r["scenarios"][1:]:
        assert sc["classified"] is True
        assert sc["summary"]["all"] == 0


def test_unsaturated_edge_never_crosses():
    """未满载管段必然从不跨割（跨未满载边的割容量 > 最大流）。"""
    r = review_network(source="S", sink="T", nodes=["A"], required_flow=1, edges=[
        {"id": "E1", "from": "S", "to": "A", "capacity": 50, "maintainable": False},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": False},
        {"id": "E3", "from": "S", "to": "T", "capacity": 60, "maintainable": False},
    ])
    normal = r["scenarios"][0]
    # 最大流 110：E1、E3 满载且横跨唯一最小割；E2 只载 50/100
    assert normal["max_flow"] == 110
    assert _grades(normal) == {"E1": "all", "E3": "all", "E2": "never"}


def test_failure_scenario_classification():
    """单管段失效后的残余网络上同样给出全最小割归属。"""
    r = review_network(source="S", sink="T", nodes=["A", "B"], required_flow=50, edges=[
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "B", "capacity": 50, "maintainable": False},
        {"id": "E3", "from": "B", "to": "T", "capacity": 100, "maintainable": False},
        {"id": "E4", "from": "S", "to": "B", "capacity": 60, "maintainable": True},
    ])
    assert r["passed"] is True
    normal, fail_e1, fail_e4 = r["scenarios"]
    # 正常网络：B→T 是唯一最小割（容量 100），裕量 50
    assert (normal["max_flow"], normal["min_cut_capacity"], normal["margin"]) == (100, 100, 50)
    assert _grades(normal) == {"E1": "never", "E2": "never", "E3": "all", "E4": "never"}
    # E1 失效：S→B 成为唯一通路，所有最小割必经；裕量 10
    assert fail_e1["removed"]["edge_id"] == "E1"
    assert (fail_e1["max_flow"], fail_e1["margin"]) == (60, 10)
    assert _grades(fail_e1) == {"E2": "never", "E3": "never", "E4": "all"}
    # E4 失效：A→B 成为瓶颈，所有最小割必经；裕量恰好为 0 仍达标
    assert fail_e4["removed"]["edge_id"] == "E4"
    assert (fail_e4["max_flow"], fail_e4["margin"]) == (50, 0)
    assert fail_e4["meets"] is True
    assert _grades(fail_e4) == {"E1": "never", "E2": "all", "E3": "never"}


def test_removed_edge_not_graded_in_its_own_scenario():
    """被移除的管段不参与该情形的分级列表。"""
    r = review_network(source="S", sink="T", nodes=[], required_flow=30, edges=[
        {"id": "E1", "from": "S", "to": "T", "capacity": 30, "maintainable": True},
        {"id": "E2", "from": "S", "to": "T", "capacity": 70, "maintainable": True},
    ])
    assert r["passed"] is True
    fail_e1 = r["scenarios"][1]
    assert fail_e1["removed"]["edge_id"] == "E1"
    assert [g["edge_id"] for g in fail_e1["edges"]] == ["E2"]
    # 仅剩 E2(70)：唯一最小割，全部最小割均跨越
    assert _grades(fail_e1) == {"E2": "all"}
    assert fail_e1["margin"] == 40


def test_failing_draft_keeps_first_failure_evidence_without_grading():
    """任一情形本已不达标：保留既有首条失败证据，不生成任何薄弱分级。"""
    edges = [
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": True},
        {"id": "E3", "from": "S", "to": "B", "capacity": 90, "maintainable": True},
        {"id": "E4", "from": "B", "to": "T", "capacity": 90, "maintainable": True},
    ]
    kwargs = dict(source="S", sink="T", nodes=["A", "B"], edges=edges, required_flow=95)
    r = review_network(**kwargs)
    audit = audit_network(**kwargs)

    assert r["passed"] is False
    # 既有首条失败证据原样保留（与审计结论一致）
    assert r["failure"] == audit["failure"]
    assert r["failure"]["position"] == 1 and r["failure"]["edge_id"] == "E1"
    assert r["failure"]["cut"]["capacity"] == 90 == r["failure"]["max_flow"]
    # 不生成任何薄弱分级
    assert len(r["scenarios"]) == 1 + 4  # 正常网络 + 4 条可检修管段
    for sc in r["scenarios"]:
        assert sc["classified"] is False
        assert sc["edges"] is None
        assert sc["min_cut_capacity"] is None
        assert sc["margin"] is None
        assert sc["summary"] is None


def test_failing_draft_without_maintainable_edges_reports_normal_stage():
    """无可检修管段且正常网络不达标：保留 stage=normal 的失败证据。"""
    r = review_network(source="S", sink="T", nodes=[], required_flow=60, edges=[
        {"id": "E1", "from": "S", "to": "T", "capacity": 50, "maintainable": False},
    ])
    assert r["passed"] is False
    assert r["failure"]["stage"] == "normal"
    assert r["failure"]["cut"]["capacity"] == 50
    (normal,) = r["scenarios"]
    assert normal["stage"] == "normal" and normal["classified"] is False


def test_review_reruns_audit_values_from_scratch():
    """复核各情形的最大可导排量与既有审计完全一致（重新执行，非缓存）。"""
    edges = [
        {"id": "E1", "from": "S", "to": "A", "capacity": 40, "maintainable": True},
        {"id": "E2", "from": "S", "to": "B", "capacity": 60, "maintainable": True},
        {"id": "E3", "from": "A", "to": "T", "capacity": 50, "maintainable": True},
        {"id": "E4", "from": "B", "to": "T", "capacity": 50, "maintainable": True},
    ]
    kwargs = dict(source="S", sink="T", nodes=["A", "B"], edges=edges, required_flow=1)
    audit = audit_network(**kwargs)
    r = review_network(**kwargs)
    audit_flows = [audit["normal"]["max_flow"]] + [s["max_flow"] for s in audit["scenarios"]]
    review_flows = [sc["max_flow"] for sc in r["scenarios"]]
    assert review_flows == audit_flows == [90, 50, 40, 50, 40]
    assert r["required_flow"] == audit["required_flow"]


def test_margin_equals_min_cut_minus_required():
    edges = [
        {"id": "E1", "from": "S", "to": "T", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "S", "to": "T", "capacity": 30, "maintainable": True},
    ]
    r = review_network(source="S", sink="T", nodes=[], edges=edges, required_flow=30)
    assert r["passed"] is True
    for sc in r["scenarios"]:
        assert sc["min_cut_capacity"] == sc["max_flow"]
        assert sc["margin"] == pytest.approx(sc["max_flow"] - 30)


@pytest.mark.parametrize(
    "kwargs, needle",
    [
        (dict(source="S", sink="S", nodes=[], edges=[], required_flow=1), "不能是同一节点"),
        (dict(source="S", sink="T", nodes=[], edges=[{"from": "S", "to": "X", "capacity": 1}], required_flow=1), "未在节点中定义"),
        (dict(source="S", sink="T", nodes=[], edges=[{"from": "S", "to": "T", "capacity": 0}], required_flow=1), "大于 0"),
        (dict(source="S", sink="T", nodes=[], edges=[], required_flow=-1), "大于 0"),
        (dict(source="", sink="T", nodes=[], edges=[], required_flow=1), "不能为空"),
    ],
)
def test_review_invalid_inputs_rejected(kwargs, needle):
    """复核与审计共用同一套业务校验。"""
    with pytest.raises(NetworkValidationError) as exc:
        review_network(**kwargs)
    assert needle in str(exc.value)
