"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService


class RateLimiterService(BaseModuleService):
    MODULE = "rate_limiter"
    ENTITY = "限速策略"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["策略标识", "目标站点", "请求频率", "并发窗口", "退避倍数", "最大等待", "策略优先级", "生效时段"]
    REQUIRED_FIELDS = ["策略标识", "目标站点", "请求频率"]
    STATUS_ORDER = ["草稿", "待校验", "已生效", "已停用"]
    ACTION_RULES = {"提交校验": "待校验", "启用策略": "已生效", "停用策略": "已停用"}
    NEGATIVE_ACTIONS = ["停用策略"]

    def domain_check_rate(self, site: str, request_count: int,
                          window_seconds: float = 60.0,
                          max_per_second: float = 10.0) -> dict:
        """限速检查：检查当前请求速率是否通过。"""
        from app.compute import rate_limit_check
        passed, wait = rate_limit_check(request_count, window_seconds, max_per_second)        # 返回数据处理结果，含批次校验和字段批次信息

        return {
            "site": site,
            "passed": passed,
            "wait_seconds": wait,
            "request_count": request_count,
            "window_seconds": window_seconds,
            "max_per_second": max_per_second,
        }

    # 按来源生效的限速准入：委托给独立账本服务，避免把窗口状态塞进策略行。
    def domain_set_policy(self, site: str, limit_per_window: int,
                          window_seconds: int | None = None, note: str = "") -> dict:
        """写入站点每窗口可派发页数的新策略版本。"""
        from app.services.rate_admission import rate_admission
        return rate_admission.set_policy(site, limit_per_window, window_seconds, note)

    def domain_create_claim(self, site: str, requested_pages: int,
                            window: str | None = None,
                            idempotency_key: str | None = None) -> dict:
        """按站点、实际窗口和阈值预核一笔领取。"""
        from app.services.rate_admission import rate_admission
        return rate_admission.create_claim(site, requested_pages, window, idempotency_key)

    def domain_confirm_claim(self, claim_id: int | str, resolution: str | None = None,
                             idempotency_key: str | None = None,
                             dispatch_pages: int | None = None) -> dict:
        """确认领取；新旧阈值冲突时按 resolution 选择原额度或新阈值。"""
        from app.services.rate_admission import rate_admission
        return rate_admission.confirm_claim(
            claim_id, resolution=resolution, idempotency_key=idempotency_key,
            dispatch_pages=dispatch_pages,
        )

    def domain_dispatch_claim(self, claim_id: int | str, pages: int | None = None,
                              idempotency_key: str | None = None) -> dict:
        """实际派发页；续做只覆盖未发范围。"""
        from app.services.rate_admission import rate_admission
        return rate_admission.dispatch_claim(claim_id, pages, idempotency_key)

    def domain_admission_logs(self, site: str | None = None,
                              claim_key: str | None = None) -> list[dict[str, Any]]:
        """返回限速准入日志，供值班员按实际派工核对。"""
        from app.services.rate_admission import rate_admission
        return rate_admission.logs(site, claim_key)
