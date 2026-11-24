"""限速准入测试：按来源、实际窗口、策略版本计算派发，并核对准入日志。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app.compute import admission_split, page_range, rate_admission_allowance
from app.services.rate_admission import rate_admission
from app.store import store


@pytest.fixture(autouse=True)
def clean_state():
    rate_admission.reset_state()
    yield
    rate_admission.reset_state()


class TestAdmissionSplit:
    def test_under_limit(self):
        assert admission_split(4, 6) == (4, 0)

    def test_over_limit(self):
        assert admission_split(4, 2) == (2, 2)

    def test_used_pages_consumed(self):
        assert admission_split(4, 6, used_pages=5) == (1, 3)

    def test_window_exhausted(self):
        assert admission_split(3, 2, used_pages=2) == (0, 3)

    def test_allowance_shape(self):
        result = rate_admission_allowance("甲站", "2026-11-24T13:00:00/60s", 2, 4)
        assert result["allowed_pages"] == 2
        assert result["deferred_pages"] == 2

    def test_page_range(self):
        assert page_range(1, 4) == "1-4"
        assert page_range(3, 2) == "3-4"
        assert page_range(5, 0) == ""


class TestPrecheck:
    def test_precheck_uses_original_window_quota(self):
        result = rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-001")
        claim = result["claim"]
        assert claim["获准页数"] == 4
        assert claim["暂缓页数"] == 0
        assert claim["待发范围"] == "1-4"
        assert claim["预核策略版本"] == 1
        assert claim["预核窗口阈值"] == 6

    def test_repeated_claim_does_not_consume_window_twice(self):
        rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-001")
        before = rate_admission.windows("甲站")[0]["预留页数"]
        repeat = rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-001")
        after = rate_admission.windows("甲站")[0]["预留页数"]
        assert repeat["repeated"] is True
        assert before == after == 4
        events = [log["事件"] for log in rate_admission.logs("甲站")]
        assert "重复领取" in events

    def test_policy_version_increments(self):
        updated = rate_admission.set_policy("甲站", 2)
        assert updated["policy"]["版本"] == 2
        assert updated["previous_limit"] == 6
        versions = [row["版本"] for row in rate_admission.policies("甲站")]
        assert versions == [1, 2]


class TestConflict:
    def _prepare_conflict(self):
        rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-001")
        rate_admission.set_policy("甲站", 2, note="窗口限额 6 -> 2")

    def test_confirm_without_resolution_reports_conflict(self):
        self._prepare_conflict()
        result = rate_admission.confirm_claim("REQ-JIA-001")
        assert result["ok"] is False
        assert result["conflict"] is True
        assert result["window_state"]["派发页数"] == 0

    def test_honor_original_dispatches_four(self):
        self._prepare_conflict()
        result = rate_admission.confirm_claim(
            "REQ-JIA-001", resolution="honor_original", idempotency_key="ACK-JIA-001")
        claim = result["claim"]
        assert result["ok"] is True
        assert result["conflict"] is True
        assert claim["派发页数"] == 4
        assert claim["暂缓页数"] == 0
        assert claim["策略版本(采用)"] == 1
        assert claim["采用窗口阈值"] == 6
        assert result["window_state"]["派发页数"] == 4

    def test_apply_current_dispatches_two_and_defers_two(self):
        self._prepare_conflict()
        result = rate_admission.confirm_claim(
            "REQ-JIA-001", resolution="apply_current", idempotency_key="ACK-JIA-001")
        claim = result["claim"]
        assert claim["派发页数"] == 2
        assert claim["暂缓页数"] == 2
        assert claim["策略版本(采用)"] == 2
        assert claim["延期窗口"]
        state = result["window_state"]
        assert state["派发页数"] == 2
        assert state["延期页数"] == 2
        log = next(item for item in rate_admission.logs("甲站")
                   if item["事件"] == "延期判定")
        assert log["延期页数"] == 2
        assert log["策略版本"] == 2
        assert log["页码范围"] == "3-4"

    def test_resend_confirm_does_not_deduct_again(self):
        self._prepare_conflict()
        first = rate_admission.confirm_claim(
            "REQ-JIA-001", resolution="honor_original", idempotency_key="ACK-JIA-001")
        second = rate_admission.confirm_claim(
            "REQ-JIA-001", resolution="honor_original", idempotency_key="ACK-JIA-001")
        assert first["claim"]["派发页数"] == 4
        assert second["repeated"] is True
        assert second["window_state"]["派发页数"] == 4

    def test_logs_pair_decision_with_actual_dispatch(self):
        self._prepare_conflict()
        rate_admission.confirm_claim(
            "REQ-JIA-001", resolution="honor_original", idempotency_key="ACK-JIA-001")
        logs = rate_admission.logs("甲站")
        decision = next(log for log in logs if log["事件"] == "准入判定")
        dispatch = next(log for log in logs if log["事件"] == "实际派发")
        assert decision["策略版本"] == 1
        assert decision["预核策略版本"] == 1
        assert dispatch["派发页数"] == 4
        assert dispatch["页码范围"] == "1-4"
        assert "v1" in dispatch["备注"]
        errors = [row for row in store.rows("error_logger")
                  if row.get("来源模块") == "限速准入"]
        assert any("新旧限额冲突" in row["错误信息"] or "延期" in row["错误信息"]
                   for row in errors) or not errors


class TestSiteIsolation:
    def test_each_site_consumes_own_quota(self):
        rate_admission.set_policy("甲站", 2)
        jia = rate_admission.create_claim("甲站", 2, idempotency_key="REQ-JIA-002")
        assert jia["claim"]["获准页数"] == 2
        yi = rate_admission.create_claim("乙站", 3, idempotency_key="REQ-YI-001")
        assert yi["claim"]["获准页数"] == 3
        jia_window = rate_admission.windows("甲站")[0]
        yi_window = rate_admission.windows("乙站")[0]
        assert jia_window["预留页数"] == 2
        assert yi_window["预留页数"] == 3

    def test_jia_quota_does_not_shrink_yi(self):
        rate_admission.set_policy("甲站", 0)
        blocked = rate_admission.create_claim("甲站", 2, idempotency_key="REQ-JIA-003")
        assert blocked["claim"]["获准页数"] == 0
        yi = rate_admission.create_claim("乙站", 3, idempotency_key="REQ-YI-002")
        assert yi["claim"]["获准页数"] == 3


class TestResume:
    def test_resume_only_dispatches_remaining(self):
        claim = rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-010")["claim"]
        rate_admission.confirm_claim(claim["领取标识"], auto_dispatch=False)
        first = rate_admission.dispatch_claim("REQ-JIA-010", pages=1, idempotency_key="D1")
        assert first["claim"]["已发范围"] == "1"
        assert first["claim"]["待发范围"] == "2-4"
        second = rate_admission.dispatch_claim("REQ-JIA-010", pages=3, idempotency_key="D2")
        assert second["claim"]["派发页数"] == 4
        assert second["claim"]["状态"] == "已派发"
        assert second["window_state"]["派发页数"] == 4

    def test_duplicate_dispatch_key_is_noop(self):
        rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-011")
        rate_admission.confirm_claim("REQ-JIA-011", auto_dispatch=False)
        first = rate_admission.dispatch_claim("REQ-JIA-011", pages=2, idempotency_key="D1")
        again = rate_admission.dispatch_claim("REQ-JIA-011", pages=2, idempotency_key="D1")
        assert first["claim"]["派发页数"] == 2
        assert again["repeated"] is True
        assert again["window_state"]["派发页数"] == 2

    def test_dispatch_beyond_claim_is_capped(self):
        rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-012")
        rate_admission.confirm_claim("REQ-JIA-012", auto_dispatch=False)
        result = rate_admission.dispatch_claim("REQ-JIA-012", pages=10, idempotency_key="D1")
        assert result["claim"]["派发页数"] == 4
        assert result["window_state"]["派发页数"] == 4

    def test_dispatch_before_confirm_rejected(self):
        rate_admission.create_claim("甲站", 4, idempotency_key="REQ-JIA-013")
        result = rate_admission.dispatch_claim("REQ-JIA-013", pages=1)
        assert result["ok"] is False
        assert rate_admission.windows("甲站")[0]["派发页数"] == 0


class TestApi:
    def _client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_scenario_via_http(self):
        client = self._client()
        created = client.post("/api/rate-admission/claims", json={
            "site": "甲站", "requested_pages": 4, "idempotency_key": "REQ-JIA-001",
        }).json()
        assert created["claim"]["获准页数"] == 4
        client.post("/api/rate-admission/policies", json={"site": "甲站", "limit_per_window": 2})
        conflict = client.post(
            "/api/rate-admission/claims/REQ-JIA-001/confirm", json={}).json()
        assert conflict["conflict"] is True
        confirmed = client.post("/api/rate-admission/claims/REQ-JIA-001/confirm", json={
            "resolution": "honor_original", "idempotency_key": "ACK-JIA-001",
        }).json()
        assert confirmed["claim"]["派发页数"] == 4
        assert confirmed["claim"]["暂缓页数"] == 0
        assert confirmed["claim"]["策略版本(采用)"] == 1
        yi = client.post("/api/rate-admission/claims", json={
            "site": "乙站", "requested_pages": 3, "idempotency_key": "REQ-YI-001",
        }).json()
        assert yi["claim"]["获准页数"] == 3
        state = client.get("/api/rate-admission/state").json()
        assert len(state["logs"]) >= 3
        assert any(log["事件"] == "实际派发" for log in state["logs"])
