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


# ---------------- 薄弱管段复核 /api/review ----------------

REVIEW_PAYLOAD = {
    "source": "S",
    "sink": "T",
    "required_flow": 50,
    "nodes": ["A", "B"],
    "edges": [
        {"id": "E1", "from": "S", "to": "A", "capacity": 100, "maintainable": True},
        {"id": "E2", "from": "A", "to": "B", "capacity": 50, "maintainable": False},
        {"id": "E3", "from": "B", "to": "T", "capacity": 100, "maintainable": False},
        {"id": "E4", "from": "S", "to": "B", "capacity": 60, "maintainable": True},
    ],
}


def test_review_pass_classifies_min_cut_membership():
    r = client.post("/api/review", json=REVIEW_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is True
    assert body["failure"] is None
    # 正常网络 + 2 条可检修管段失效情形
    assert [sc["stage"] for sc in body["scenarios"]] == [
        "normal", "single_failure", "single_failure",
    ]
    normal = body["scenarios"][0]
    assert normal["classified"] is True
    assert normal["min_cut_capacity"] == 100 == normal["max_flow"]
    assert normal["margin"] == 50
    grades = {g["edge_id"]: g["crosses"] for g in normal["edges"]}
    assert grades == {"E1": "never", "E2": "never", "E3": "all", "E4": "never"}
    # E1 失效后 S→B(E4) 成为唯一通路，所有最小割必经
    fail_e1 = body["scenarios"][1]
    assert fail_e1["removed"]["edge_id"] == "E1"
    assert fail_e1["margin"] == 10
    assert {g["edge_id"]: g["crosses"] for g in fail_e1["edges"]}["E4"] == "all"


def test_review_fail_keeps_first_failure_evidence_without_grading():
    r = client.post("/api/review", json=FAIL_PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["passed"] is False
    f = body["failure"]
    assert f["position"] == 1 and f["edge_id"] == "E1"
    assert f["max_flow"] == 90
    assert f["cut"]["capacity"] == 90
    assert "S" in f["cut"]["source_side_nodes"]
    assert "T" in f["cut"]["sink_side_nodes"]
    # 不达标情形不得生成薄弱分级
    for sc in body["scenarios"]:
        assert sc["classified"] is False
        assert sc["edges"] is None
        assert sc["margin"] is None


def test_review_invalid_node_reference_400():
    bad = dict(REVIEW_PAYLOAD)
    bad["edges"] = [{"from": "S", "to": "幽灵节点", "capacity": 10, "maintainable": True}]
    r = client.post("/api/review", json=bad)
    assert r.status_code == 400
    assert "未在节点中定义" in r.json()["error"]


def test_review_non_json_body_400():
    r = client.post("/api/review", content=b"not-json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
