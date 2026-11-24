"""限速准入接口：策略版本、领取预核、冲突确认、实际派发和准入日志。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.services.rate_admission import rate_admission

router = APIRouter(prefix="/api/rate-admission", tags=["限速准入"])


@router.get("/state")
def admission_state(site: str | None = Query(default=None)) -> dict[str, Any]:
    """一次拉取策略、窗口账本、领取和准入日志，供值班台核对。"""
    rate_admission.ensure_seed()
    return {
        "policies": rate_admission.policies(site),
        "windows": rate_admission.windows(site),
        "claims": rate_admission.claims(site),
        "logs": rate_admission.logs(site),
    }


@router.get("/policies")
def list_policies(site: str | None = Query(default=None)) -> dict[str, Any]:
    return {"items": rate_admission.policies(site)}


@router.post("/policies")
def set_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """写入站点策略的新版本；窗口阈值变化会产生版本号递增。"""
    return rate_admission.set_policy(
        site=payload.get("site", ""),
        limit_per_window=payload.get("limit_per_window", 0),
        window_seconds=payload.get("window_seconds"),
        note=payload.get("note", ""),
        operator=payload.get("operator", "值班员"),
    )


@router.get("/claims")
def list_claims(
    site: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    return {"items": rate_admission.claims(site, status)}


@router.get("/claims/{claim_id}")
def get_claim(claim_id: str) -> dict[str, Any]:
    claim = rate_admission.find_claim(claim_id)
    if claim is None:
        return {"ok": False, "message": f"领取 {claim_id} 不存在"}
    return {"ok": True, "claim": dict(claim), "window_state": rate_admission._window_state(claim)}


@router.post("/claims")
def create_claim(payload: dict[str, Any]) -> dict[str, Any]:
    """按站点当前策略预核领取；同一幂等键重送只回放原判定。"""
    return rate_admission.create_claim(
        site=payload.get("site", ""),
        requested_pages=payload.get("requested_pages", 0),
        window=payload.get("window"),
        idempotency_key=payload.get("idempotency_key"),
        expected_policy_version=payload.get("expected_policy_version"),
        operator=payload.get("operator", "值班员"),
    )


@router.post("/claims/{claim_id}/confirm")
def confirm_claim(claim_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """确认领取。新旧阈值冲突时须给出 honor_original 或 apply_current。"""
    return rate_admission.confirm_claim(
        claim_id,
        resolution=payload.get("resolution"),
        idempotency_key=payload.get("idempotency_key"),
        dispatch_pages=payload.get("dispatch_pages"),
        auto_dispatch=bool(payload.get("auto_dispatch", True)),
        operator=payload.get("operator", "值班员"),
    )


@router.post("/claims/{claim_id}/dispatch")
def dispatch_claim(claim_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """实际派发；中断后续做只处理未发范围，已发页不会再次扣减。"""
    return rate_admission.dispatch_claim(
        claim_id,
        pages=payload.get("pages"),
        idempotency_key=payload.get("idempotency_key"),
        operator=payload.get("operator", "值班员"),
    )


@router.get("/logs")
def list_logs(
    site: str | None = Query(default=None),
    claim_key: str | None = Query(default=None),
) -> dict[str, Any]:
    return {"items": rate_admission.logs(site, claim_key)}
