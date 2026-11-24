"""限速准入领域服务：按来源、实际窗口和策略版本计算派发页并记录准入日志。

设计要点：
- 策略按站点独立维护版本；同一个窗口的额度只属于本来源，不同来源互不占用。
- 窗口账本把“已派发”和“暂缓/预留”分开记账：
  ``dispatched_pages`` 是实际发出的页数，``reserved_pages`` 是预核通过但还没发的页，
  ``deferred_pages`` 是判定留到后续窗口的页。
- 领取（claim）带幂等键。重复领取、重复确认、重复派发都只回放原判定，不再次扣减窗口。
- 一次领取可以分批派发：中断后继续只处理未发范围，已发范围不会重复扣减。
"""
from __future__ import annotations

import time
from typing import Any

from app.compute import admission_split, page_range
from app.store import store

POLICY_TABLE = "_rate_policy"
CLAIM_TABLE = "_rate_claim"
WINDOW_TABLE = "_rate_window"
LOG_TABLE = "_rate_admission_log"

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_POLICY_ROWS: list[dict[str, Any]] = [
    {
        "id": 1,
        "站点": "甲站",
        "版本": 1,
        "窗口阈值": 6,
        "窗口秒数": 60,
        "状态": "生效",
        "更新时间": "2026-11-24 09:00:00",
        "备注": "原窗口额度：每窗口 6 页",
    },
    {
        "id": 2,
        "站点": "乙站",
        "版本": 1,
        "窗口阈值": 6,
        "窗口秒数": 60,
        "状态": "生效",
        "更新时间": "2026-11-24 09:00:00",
        "备注": "乙站独立额度：每窗口 6 页",
    },
]


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _next_window_label(window: str, seconds: int) -> str:
    """由当前窗口标签推下一个窗口标签；无法解析时退回可读的 next 标记。"""
    head = window.split("/", 1)[0]
    try:
        stamp = time.mktime(time.strptime(head, "%Y-%m-%dT%H:%M:%S")) + seconds
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp)) + f"/{seconds}s"
    except (ValueError, OverflowError):
        return f"{window}->next"


class RateAdmissionService:
    """按来源策略处理领取、确认、派发，并落限速准入日志。"""

    # ---------------------------------------------------------------- 基础数据

    def ensure_seed(self) -> None:
        """首次访问时补齐甲站/乙站的示例策略，重复调用不会覆盖已有版本。"""
        rows = store.rows(POLICY_TABLE)
        if rows:
            return
        for row in DEFAULT_POLICY_ROWS:
            rows.append(dict(row))

    def reset_state(self) -> None:
        """清空运行期账本并恢复示例策略，供测试和演示重放使用。"""
        for table in (POLICY_TABLE, CLAIM_TABLE, WINDOW_TABLE, LOG_TABLE):
            store.rows(table).clear()
        self.ensure_seed()

    def policies(self, site: str | None = None) -> list[dict[str, Any]]:
        self.ensure_seed()
        rows = [
            dict(row) for row in store.rows(POLICY_TABLE)
            if not site or row.get("站点") == site
        ]
        return sorted(rows, key=lambda row: (row.get("站点", ""), int(row.get("版本", 0))))

    def active_policy(self, site: str) -> dict[str, Any] | None:
        self.ensure_seed()
        candidates = [
            row for row in store.rows(POLICY_TABLE)
            if row.get("站点") == site and row.get("状态") == "生效"
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda row: int(row.get("版本", 0)))

    def set_policy(
        self,
        site: str,
        limit_per_window: int,
        window_seconds: int | None = None,
        note: str = "",
        operator: str = "system",
    ) -> dict[str, Any]:
        """写入新的策略版本；旧版本保留为“已替换”，便于回看判定依据。"""
        self.ensure_seed()
        site = str(site or "").strip()
        if not site:
            return {"ok": False, "message": "站点不能为空"}
        try:
            limit = int(limit_per_window)
        except (TypeError, ValueError):
            return {"ok": False, "message": "窗口阈值必须是整数"}
        if limit < 0:
            return {"ok": False, "message": "窗口阈值不能为负数"}
        current = self.active_policy(site)
        seconds = int(window_seconds or (current or {}).get("窗口秒数") or DEFAULT_WINDOW_SECONDS)
        if seconds <= 0:
            return {"ok": False, "message": "窗口秒数必须大于 0"}
        rows = store.rows(POLICY_TABLE)
        previous_version = int(current.get("版本", 0)) if current else 0
        for row in rows:
            if row.get("站点") == site and row.get("状态") == "生效":
                row["状态"] = "已替换"
                row["更新时间"] = _now_text()
        version = previous_version + 1
        policy = {
            "id": _next_id(rows),
            "站点": site,
            "版本": version,
            "窗口阈值": limit,
            "窗口秒数": seconds,
            "状态": "生效",
            "更新时间": _now_text(),
            "备注": note or f"{operator} 调整窗口阈值为 {limit} 页",
        }
        rows.append(policy)
        return {
            "ok": True,
            "message": f"{site} 策略已更新到版本 v{version}，窗口阈值 {limit} 页",
            "policy": dict(policy),
            "previous_version": previous_version,
            "previous_limit": int(current.get("窗口阈值", 0)) if current else None,
        }

    def window_label(self, window_seconds: int | None = None, now: float | None = None) -> str:
        seconds = int(window_seconds or DEFAULT_WINDOW_SECONDS)
        stamp = int((now if now is not None else time.time()) // seconds) * seconds
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp)) + f"/{seconds}s"

    def get_window(self, site: str, window: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = store.rows(WINDOW_TABLE)
        for row in rows:
            if row.get("站点") == site and row.get("窗口") == window:
                return row
        row = {
            "id": _next_id(rows),
            "站点": site,
            "窗口": window,
            "派发页数": 0,
            "预留页数": 0,
            "延期页数": 0,
            "策略版本": int((policy or {}).get("版本", 0)),
            "窗口阈值": int((policy or {}).get("窗口阈值", 0)),
            "更新时间": _now_text(),
        }
        rows.append(row)
        return row

    def windows(self, site: str | None = None) -> list[dict[str, Any]]:
        return [
            dict(row) for row in store.rows(WINDOW_TABLE)
            if not site or row.get("站点") == site
        ]

    # ---------------------------------------------------------------- 领取流程

    def claims(self, site: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = [
            dict(row) for row in store.rows(CLAIM_TABLE)
            if (not site or row.get("站点") == site)
            and (not status or row.get("状态") == status)
        ]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    def find_claim(self, claim_id: int | str) -> dict[str, Any] | None:
        for row in store.rows(CLAIM_TABLE):
            if str(row.get("id")) == str(claim_id) or claim_id in (
                row.get("领取标识"), row.get("幂等键"),
            ):
                return row
        return None

    def create_claim(
        self,
        site: str,
        requested_pages: int,
        window: str | None = None,
        idempotency_key: str | None = None,
        expected_policy_version: int | None = None,
        operator: str = "system",
    ) -> dict[str, Any]:
        """按当前站点策略预核领取；重复幂等键只回放原判定，不再占用窗口。"""
        self.ensure_seed()
        site = str(site or "").strip()
        if not site:
            return {"ok": False, "message": "站点不能为空"}
        try:
            requested = int(requested_pages)
        except (TypeError, ValueError):
            return {"ok": False, "message": "申请页数必须是整数"}
        if requested <= 0:
            return {"ok": False, "message": "申请页数必须大于 0"}

        if idempotency_key:
            existing = next(
                (row for row in store.rows(CLAIM_TABLE)
                 if row.get("幂等键") == idempotency_key),
                None,
            )
            if existing is not None:
                if existing.get("站点") != site or int(existing.get("申请页数", 0)) != requested:
                    return {"ok": False, "message": "幂等键已被另一笔领取占用，不能改作他用"}
                self._log(
                    existing, event="重复领取", decision="无变化",
                    note="同一幂等键重送，回放原判定，窗口未再次扣减",
                    dispatched=0, deferred=0, page="",
                )
                return {
                    "ok": True,
                    "message": "重复领取：返回原判定，未再次扣减窗口额度",
                    "repeated": True,
                    "claim": dict(existing),
                    "window_state": self._window_state(existing),
                }

        policy = self.active_policy(site)
        if policy is None:
            return {"ok": False, "message": f"{site} 未配置生效的限速策略"}
        version = int(policy.get("版本", 0))
        limit = int(policy.get("窗口阈值", 0))
        if expected_policy_version is not None and int(expected_policy_version) != version:
            return {
                "ok": False,
                "message": f"{site} 策略已从 v{expected_policy_version} 更新到 v{version}，请刷新后重领",
                "policy_version": version,
            }

        window_label = window or self.window_label(int(policy.get("窗口秒数") or DEFAULT_WINDOW_SECONDS))
        wrow = self.get_window(site, window_label, policy)
        occupied = int(wrow.get("派发页数", 0)) + int(wrow.get("预留页数", 0))
        allowed, deferred = admission_split(requested, limit, occupied)

        claim_id = _next_id(store.rows(CLAIM_TABLE))
        claim = {
            "id": claim_id,
            "领取标识": f"CLM-{claim_id:04d}",
            "幂等键": idempotency_key or f"CLM-{claim_id:04d}",
            "站点": site,
            "窗口": window_label,
            "申请页数": requested,
            "预核策略版本": version,
            "预核窗口阈值": limit,
            "获准页数": allowed,
            "暂缓页数": deferred,
            "派发页数": 0,
            "待发页数": allowed,
            "已发范围": "",
            "待发范围": page_range(1, allowed),
            "延期窗口": _next_window_label(window_label, int(policy.get("窗口秒数") or DEFAULT_WINDOW_SECONDS))
            if deferred else "",
            "策略版本(采用)": version,
            "采用窗口阈值": limit,
            "处理方式": "",
            "冲突": False,
            "状态": "已预核",
            "创建时间": _now_text(),
            "更新时间": _now_text(),
            "派发幂等键": [],
            "操作人": operator,
        }
        store.rows(CLAIM_TABLE).append(claim)
        wrow["预留页数"] = int(wrow.get("预留页数", 0)) + allowed
        wrow["策略版本"] = version
        wrow["窗口阈值"] = limit
        wrow["更新时间"] = _now_text()

        decision = "允许" if deferred == 0 else ("部分允许" if allowed else "暂缓")
        note = (
            f"按 {site} 策略 v{version}（{limit} 页/窗口）预核："
            f"申请 {requested} 页，获准 {allowed} 页"
        )
        if deferred:
            note += f"，暂缓 {deferred} 页留至下一窗口 {claim['延期窗口']}"
        self._log(claim, event="准入判定", decision=decision, note=note,
                  page=page_range(1, allowed), deferred=deferred)
        if deferred:
            self._log(claim, event="延期判定", decision="暂缓",
                      note=f"未获准 {deferred} 页，留至下一窗口 {claim['延期窗口']}",
                      page=page_range(allowed + 1, deferred), deferred=deferred)
            self._record_error("RATE-DEFERRED", claim,
                               f"限速准入暂缓 {deferred} 页，策略 v{version} / 阈值 {limit}")
        return {
            "ok": True,
            "message": f"{claim['领取标识']} 预核完成：获准 {allowed} 页，暂缓 {deferred} 页",
            "repeated": False,
            "claim": dict(claim),
            "policy": dict(policy),
            "window_state": self._window_state(claim),
        }

    def confirm_claim(
        self,
        claim_id: int | str,
        resolution: str | None = None,
        idempotency_key: str | None = None,
        dispatch_pages: int | None = None,
        auto_dispatch: bool = True,
        operator: str = "system",
    ) -> dict[str, Any]:
        """确认领取；遇新旧阈值冲突必须先选 honor_original 或 apply_current。"""
        claim = self.find_claim(claim_id)
        if claim is None:
            return {"ok": False, "message": f"领取 {claim_id} 不存在"}
        if claim.get("状态") in ("已确认", "部分派发", "已派发", "已延期"):
            self._log(claim, event="重复确认", decision="无变化",
                      note="领取已确认，重复请求只回放结果，不再扣减窗口", dispatched=0, deferred=0)
            return self._claim_result(
                claim, ok=True, repeated=True,
                message="重复确认：返回原派发结果，窗口未再次扣减",
            )

        if resolution not in (None, "", "honor_original", "apply_current"):
            return {"ok": False, "message": "处理方式只能是 honor_original（沿用原额度）或 apply_current（按新阈值）"}

        policy = self.active_policy(claim["站点"])
        if policy is None:
            return {"ok": False, "message": f"{claim['站点']} 当前没有生效策略，无法确认"}
        current_version = int(policy.get("版本", 0))
        current_limit = int(policy.get("窗口阈值", 0))
        conflict = (
            current_version != int(claim.get("预核策略版本", 0))
            or current_limit != int(claim.get("预核窗口阈值", 0))
        )
        claim["冲突"] = conflict
        if conflict and not resolution:
            return {
                "ok": False,
                "message": (
                    f"新旧限额冲突：领取按 v{claim.get('预核策略版本')}（{claim.get('预核窗口阈值')} 页）预核，"
                    f"当前生效 v{current_version}（{current_limit} 页）；"
                    "请选择 honor_original 沿用原额度，或 apply_current 按新阈值"
                ),
                "conflict": True,
                "claim": dict(claim),
                "current_policy": dict(policy),
                "window_state": self._window_state(claim),
            }

        wrow = self.get_window(claim["站点"], claim["窗口"], policy)
        # 释放本笔预核时占用的预留，再按选定口径重新占用，避免重复预留。
        pending = int(claim.get("待发页数", 0))
        wrow["预留页数"] = max(int(wrow.get("预留页数", 0)) - pending, 0)
        occupied = int(wrow.get("派发页数", 0)) + int(wrow.get("预留页数", 0))

        if resolution == "honor_original":
            basis = "原额度"
            used_version = int(claim.get("预核策略版本", current_version))
            used_limit = int(claim.get("预核窗口阈值", current_limit))
        else:
            basis = "按当前阈值" if conflict else "当前阈值"
            used_version = current_version
            used_limit = current_limit
        allowed, deferred = admission_split(int(claim.get("申请页数", 0)), used_limit, occupied)

        claim["获准页数"] = allowed
        claim["暂缓页数"] = deferred
        claim["待发页数"] = allowed
        claim["待发范围"] = page_range(1, allowed)
        claim["已发范围"] = ""
        claim["派发页数"] = 0
        claim["策略版本(采用)"] = used_version
        claim["采用窗口阈值"] = used_limit
        claim["处理方式"] = resolution or "none"
        claim["状态"] = "已确认"
        claim["更新时间"] = _now_text()
        wrow["预留页数"] = int(wrow.get("预留页数", 0)) + allowed
        wrow["策略版本"] = used_version
        wrow["窗口阈值"] = used_limit
        wrow["更新时间"] = _now_text()
        if deferred:
            claim["延期窗口"] = _next_window_label(
                claim["窗口"], int(policy.get("窗口秒数") or DEFAULT_WINDOW_SECONDS))
            wrow["延期页数"] = int(wrow.get("延期页数", 0)) + deferred
        else:
            claim["延期窗口"] = ""

        conflict_note = ""
        if conflict:
            conflict_note = (
                f"新旧限额冲突：预核 v{claim.get('预核策略版本')}（{claim.get('预核窗口阈值')} 页），"
                f"当前 v{current_version}（{current_limit} 页）；本次{basis} v{used_version}"
            )
        self._log(claim, event="准入判定", decision="允许" if deferred == 0 else "部分允许",
                  note=conflict_note or f"无冲突，按 v{used_version} 确认",
                  page=page_range(1, allowed), deferred=deferred)
        if deferred:
            self._log(claim, event="延期判定", decision="暂缓",
                      note=f"按{basis} v{used_version} 判定 {deferred} 页留至下一窗口 {claim['延期窗口']}",
                      page=page_range(allowed + 1, deferred), deferred=deferred)
            self._record_error("RATE-CONFLICT" if conflict else "RATE-DEFERRED", claim,
                               f"限速准入延期 {deferred} 页，采用策略 v{used_version} / 阈值 {used_limit}")

        if auto_dispatch and allowed > 0:
            pages = allowed if dispatch_pages is None else int(dispatch_pages)
            if pages <= 0:
                pages = allowed
            dispatch_result = self.dispatch_claim(
                claim["领取标识"], pages=pages, idempotency_key=idempotency_key,
                operator=operator,
            )
            dispatch_result.update({
                "message": f"已按{basis}确认：派发 {claim.get('派发页数')} 页，暂缓 {claim.get('暂缓页数')} 页",
                "conflict": conflict,
            })
            return dispatch_result

        if allowed == 0:
            claim["状态"] = "已延期"
        return self._claim_result(claim, ok=True, message=f"已按{basis}确认：获准 {allowed} 页，暂缓 {deferred} 页")

    def dispatch_claim(
        self,
        claim_id: int | str,
        pages: int | None = None,
        idempotency_key: str | None = None,
        operator: str = "system",
    ) -> dict[str, Any]:
        """实际派发；同一派发幂等键只生效一次，续做只覆盖未发范围。"""
        claim = self.find_claim(claim_id)
        if claim is None:
            return {"ok": False, "message": f"领取 {claim_id} 不存在"}
        if claim.get("状态") == "已预核":
            # 预核后必须先确认；确认时才处理新旧阈值冲突并重算额度。
            return {"ok": False, "message": "领取尚未确认，请先确认（遇冲突需选择 honor_original 或 apply_current）"}

        if idempotency_key and idempotency_key in claim.get("派发幂等键", []):
            self._log(claim, event="重复派发", decision="无变化",
                      note="同一派发幂等键重送，只回放结果，未再次扣减窗口", dispatched=0, deferred=0)
            return self._claim_result(
                claim, ok=True, repeated=True,
                message="重复派发：返回原派发结果，窗口未再次扣减",
            )

        pending = int(claim.get("待发页数", 0))
        if pending <= 0:
            self._log(claim, event="重复派发", decision="无变化",
                      note="本次领取已无未发范围，重复派发未扣减窗口", dispatched=0, deferred=0)
            return self._claim_result(
                claim, ok=True, repeated=True,
                message="本次领取已全部派发，无未发范围可续做",
            )

        if pages is None:
            batch = pending
        else:
            try:
                batch = int(pages)
            except (TypeError, ValueError):
                return {"ok": False, "message": "派发页数必须是整数"}
            if batch <= 0:
                return {"ok": False, "message": "派发页数必须大于 0"}
            batch = min(batch, pending)

        start = int(claim.get("派发页数", 0)) + 1
        claim["派发页数"] = int(claim.get("派发页数", 0)) + batch
        claim["待发页数"] = pending - batch
        claim["已发范围"] = page_range(1, int(claim["派发页数"]))
        claim["待发范围"] = page_range(int(claim["派发页数"]) + 1, int(claim["待发页数"]))
        claim["状态"] = "已派发" if int(claim["待发页数"]) == 0 else "部分派发"
        claim["更新时间"] = _now_text()
        if idempotency_key:
            claim.setdefault("派发幂等键", []).append(idempotency_key)

        wrow = self.get_window(claim["站点"], claim["窗口"])
        wrow["派发页数"] = int(wrow.get("派发页数", 0)) + batch
        wrow["预留页数"] = max(int(wrow.get("预留页数", 0)) - batch, 0)
        wrow["策略版本"] = int(claim.get("策略版本(采用)", wrow.get("策略版本", 0)))
        wrow["窗口阈值"] = int(claim.get("采用窗口阈值", wrow.get("窗口阈值", 0)))
        wrow["更新时间"] = _now_text()

        self._log(
            claim, event="实际派发", decision="派发",
            page=page_range(start, batch), dispatched=batch, deferred=0,
            note=f"第 {page_range(start, batch)} 页实际派发，策略 v{claim.get('策略版本(采用)')}",
        )
        return self._claim_result(claim, ok=True, message=f"已派发 {batch} 页")

    def logs(self, site: str | None = None, claim_key: str | None = None) -> list[dict[str, Any]]:
        rows = [
            dict(row) for row in store.rows(LOG_TABLE)
            if (not site or row.get("站点") == site)
            and (not claim_key or row.get("领取标识") == claim_key or row.get("幂等键") == claim_key)
        ]
        return sorted(rows, key=lambda row: int(row.get("id", 0)))

    # ---------------------------------------------------------------- 内部辅助

    def _claim_result(
        self, claim: dict[str, Any], *, ok: bool, message: str, repeated: bool = False,
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "message": message,
            "repeated": repeated,
            "claim": dict(claim),
            "window_state": self._window_state(claim),
        }

    def _window_state(self, claim: dict[str, Any]) -> dict[str, Any]:
        wrow = self.get_window(claim["站点"], claim["窗口"])
        policy = self.active_policy(claim["站点"])
        dispatched = int(wrow.get("派发页数", 0))
        reserved = int(wrow.get("预留页数", 0))
        snapshot_limit = int(claim.get("预核窗口阈值", 0))
        current_limit = int((policy or {}).get("窗口阈值", 0))
        current_version = int((policy or {}).get("版本", 0))
        return {
            "站点": claim["站点"],
            "窗口": claim["窗口"],
            "派发页数": dispatched,
            "预留页数": reserved,
            "延期页数": int(wrow.get("延期页数", 0)),
            "本次已发范围": claim.get("已发范围", ""),
            "本次待发范围": claim.get("待发范围", ""),
            "预核窗口阈值": snapshot_limit,
            "预核策略版本": int(claim.get("预核策略版本", 0)),
            "按预核阈值剩余": max(snapshot_limit - dispatched, 0),
            "当前窗口阈值": current_limit,
            "当前策略版本": current_version,
            "按当前阈值剩余": max(current_limit - dispatched, 0),
        }

    def _log(
        self,
        claim: dict[str, Any],
        *,
        event: str,
        decision: str,
        note: str = "",
        page: str = "",
        dispatched: int = 0,
        deferred: int = 0,
    ) -> dict[str, Any]:
        rows = store.rows(LOG_TABLE)
        entry = {
            "id": _next_id(rows),
            "日志标识": f"RAL-{_next_id(rows):04d}",
            "站点": claim.get("站点", ""),
            "窗口": claim.get("窗口", ""),
            "领取标识": claim.get("领取标识", ""),
            "幂等键": claim.get("幂等键", ""),
            "事件": event,
            "判定": decision,
            "策略版本": int(claim.get("策略版本(采用)", claim.get("预核策略版本", 0))),
            "预核策略版本": int(claim.get("预核策略版本", 0)),
            "窗口阈值": int(claim.get("预核窗口阈值", 0)),
            "采用窗口阈值": int(claim.get("采用窗口阈值", claim.get("预核窗口阈值", 0))),
            "申请页数": int(claim.get("申请页数", 0)),
            "获准页数": int(claim.get("获准页数", 0)),
            "派发页数": int(dispatched),
            "延期页数": int(deferred),
            "页码范围": page,
            "领取已发范围": claim.get("已发范围", ""),
            "领取待发范围": claim.get("待发范围", ""),
            "判定依据": claim.get("处理方式") or "预核",
            "备注": note,
            "记录时间": _now_text(),
        }
        rows.append(entry)
        return entry

    def _record_error(self, code: str, claim: dict[str, Any], message: str) -> None:
        """把冲突/延期写进错误日志，错误信息里带上准入日志可对照的信息。"""
        from app.services.error_logger import ErrorLoggerService

        logger = ErrorLoggerService()
        logger.domain_log_error(
            code,
            f"{message}；领取 {claim.get('领取标识')}，窗口 {claim.get('窗口')}",
            "限速准入",
            trace_id=str(claim.get("领取标识", "")),
            severity="warning" if code == "RATE-DEFERRED" else "error",
        )


rate_admission = RateAdmissionService()
