"""业务 API 测试：健康检查、审计放行/失败证据、非法输入 400、静态页面。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PASS_PAYLOAD = {
    "source": "S",
    "sink": "T",
    "required_flow": 95,
    "nodes": ["A", "B"],
    "edges": [
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": True},
        {"id": "E3", "from": "S", "to": "B", "capacity": 100, "maintainable": True},
        {"id": "E4", "from": "B", "to": "T", "capacity": 100, "maintainable": True},
    ],
}

FAIL_PAYLOAD = {
    "source": "S",
    "sink": "T",
    "required_flow": 95,
    "nodes": ["A", "B"],
    "edges": [
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": True},
        {"id": "E3", "from": "S", "to": "B", "capacity": 90, "maintainable": True},
        {"id": "E4", "from": "B", "to": "T", "capacity": 90, "maintainable": True},
    ],
}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_page_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "事故导排" in r.text


def test_audit_pass():
    r = client.post("/api/audit", json=PASS_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True
    assert body["normal"]["max_flow"] == 200
    assert len(body["scenarios"]) == 4
    assert all(s["meets"] for s in body["scenarios"])
    assert body["failure"] is None


def test_audit_fail_returns_first_edge_and_cut():
    r = client.post("/api/audit", json=FAIL_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is False
    f = body["failure"]
    assert f["position"] == 1 and f["edge_id"] == "E1"
    assert f["max_flow"] == 90
    cut = f["cut"]
    assert cut["capacity"] == 90
    assert "S" in cut["source_side_nodes"]
    assert "T" in cut["sink_side_nodes"]
    assert len(cut["cut_edges"]) >= 1


def test_audit_invalid_node_reference_400():
    bad = dict(PASS_PAYLOAD)
    bad["edges"] = [{"from": "S", "to": "不存在", "capacity": 10, "maintainable": True}]
    r = client.post("/api/audit", json=bad)
    assert r.status_code == 400
    assert "未在节点中定义" in r.json()["error"]


def test_audit_invalid_capacity_400():
    bad = dict(PASS_PAYLOAD)
    bad["edges"] = [{"from": "S", "to": "T", "capacity": 0, "maintainable": True}]
    r = client.post("/api/audit", json=bad)
    assert r.status_code == 400
    assert "大于 0" in r.json()["error"]


def test_audit_invalid_direction_self_loop_400():
    bad = dict(PASS_PAYLOAD)
    bad["edges"] = [{"from": "S", "to": "S", "capacity": 10, "maintainable": True}]
    r = client.post("/api/audit", json=bad)
    assert r.status_code == 400
    assert "起点和终点不能相同" in r.json()["error"]


def test_audit_non_json_body_400():
    r = client.post("/api/audit", content=b"not-json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_review_pass_classifies_edges_per_scenario():
    r = client.post("/api/review", json=PASS_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["reviewable"] is True
    assert body["failure"] is None
    # 正常网络 + 4 条可检修管段，逐情形给出裕量与分级
    assert len(body["scenarios"]) == 5
    normal = body["scenarios"][0]
    assert normal["stage"] == "normal"
    assert normal["min_cut_capacity"] == normal["max_flow"] == 200
    assert normal["margin"] == 105
    # 并联干线：每条管段仅被部分最小割跨越，没有“全部必经”
    assert all(e["class"] == "some" for e in normal["edges"])
    for scen in body["scenarios"]:
        assert scen["min_cut_capacity"] == scen["max_flow"]
        assert scen["margin"] == scen["max_flow"] - 95
        assert {e["class"] for e in scen["edges"]} <= {"all", "some", "never"}


def test_review_unique_bottleneck_all_vs_never():
    payload = {
        "source": "S", "sink": "T", "required_flow": 100, "nodes": ["A"],
        "edges": [
            {"id": "E1", "from": "S", "to": "A", "capacity": 200, "maintainable": False},
            {"id": "E2", "from": "A", "to": "T", "capacity": 100, "maintainable": False},
        ],
    }
    r = client.post("/api/review", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["reviewable"] is True
    (normal,) = body["scenarios"]
    by_id = {e["id"]: e["class"] for e in normal["edges"]}
    assert by_id == {"E1": "never", "E2": "all"}
    assert normal["margin"] == 0


def test_review_fail_keeps_first_failure_evidence_without_grading():
    r = client.post("/api/review", json=FAIL_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["reviewable"] is False
    assert body["scenarios"] is None  # 不达标情形不生成薄弱分级
    f = body["failure"]
    assert f["position"] == 1 and f["edge_id"] == "E1"
    assert f["cut"]["capacity"] == f["max_flow"] == 90
    assert "S" in f["cut"]["source_side_nodes"]
    assert "T" in f["cut"]["sink_side_nodes"]


def test_review_invalid_node_reference_400():
    bad = dict(PASS_PAYLOAD)
    bad["edges"] = [{"from": "S", "to": "不存在", "capacity": 10, "maintainable": True}]
    r = client.post("/api/review", json=bad)
    assert r.status_code == 400
    assert "未在节点中定义" in r.json()["error"]


def test_review_non_json_body_400():
    r = client.post("/api/review", content=b"not-json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
