"""通用计算引擎：跨模块复用的纯计算函数，不依赖任何外部框架。"""
from __future__ import annotations

import hashlib
import math
import time
from difflib import SequenceMatcher
from typing import Any


def content_hash(content: str | bytes, algorithm: str = "sha256") -> str:
    """对内容计算哈希摘要。"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    h = hashlib.new(algorithm)
    h.update(content)
    return h.hexdigest()


def hash_dedup(
    items: list[dict[str, Any]],
    key_fields: list[str],
    algorithm: str = "sha256",
) -> tuple[list[dict[str, Any]], int]:
    """基于内容哈希对数据行去重，返回去重结果和去重数。"""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        parts = [str(item.get(f, "")) for f in key_fields]
        h = content_hash("|".join(parts), algorithm)
        if h not in seen:
            seen.add(h)
            result.append(item)
    return result, len(items) - len(result)


def text_similarity(a: str, b: str) -> float:
    """计算两段文本的相似度，返回值 0.0~1.0。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def rate_limit_check(
    request_count: int,
    window_seconds: float,
    max_per_second: float,
    now: float | None = None,
) -> tuple[bool, float]:
    """限速检查：返回 (是否通过, 建议等待秒数)。"""
    if now is None:
        now = time.time()
    if window_seconds <= 0 or max_per_second <= 0:
        return True, 0.0
    max_in_window = max_per_second * window_seconds
    if request_count <= max_in_window:
        return True, 0.0
    wait = (request_count - max_in_window) / max_per_second
    return False, max(wait, 0.0)


def batch_partition(
    items: list[Any],
    batch_size: int,
    min_batches: int = 0,
) -> list[list[Any]]:
    """将数据行按指定大小切片，并保证最小切片数。"""
    if not items:
        return []
    if len(items) <= min_batches * batch_size:
        actual_size = max(1, math.ceil(len(items) / max(min_batches, 1)))
    else:
        actual_size = max(1, batch_size)
    partitions: list[list[Any]] = []
    for i in range(0, len(items), actual_size):
        partitions.append(items[i:i + actual_size])
    if len(partitions) < min_batches and partitions:
        while len(partitions) < min_batches:
            partitions.append([])
    return partitions


def retry_calculator(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
    multiplier: float = 2.0,
    jitter: bool = True,
) -> float:
    """指数退避重试延迟计算器，返回下次等待秒数。"""
    import random
    delay = base_delay * (multiplier ** max(attempt - 1, 0))
    delay = min(delay, max_delay)
    if jitter:
        delay = delay * (0.5 + random.random() * 0.5)
    return round(delay, 3)

def admission_split(
    requested_pages: int,
    limit_per_window: int,
    used_pages: int = 0,
) -> tuple[int, int]:
    """限速准入拆分：按窗口阈值计算可派发页数和暂缓页数。

    ``used_pages`` 是该来源在该窗口已经实际派发的页数；同一来源的不同窗口
    互不影响，不同来源也不共享额度。返回值 ``(allowed, deferred)``。
    """
    requested = max(int(requested_pages), 0)
    limit = max(int(limit_per_window), 0)
    used = max(int(used_pages), 0)
    allowed = min(requested, max(limit - used, 0))
    return allowed, requested - allowed


def rate_admission_allowance(
    site: str,
    window: str,
    limit_per_window: int,
    requested_pages: int,
    used_pages: int = 0,
) -> dict[str, Any]:
    """用站点、实际窗口和阈值计算允许派发的页数（纯函数，便于核对）。"""
    allowed, deferred = admission_split(requested_pages, limit_per_window, used_pages)
    return {
        "site": site,
        "window": window,
        "limit_per_window": max(int(limit_per_window), 0),
        "requested_pages": max(int(requested_pages), 0),
        "used_pages": max(int(used_pages), 0),
        "allowed_pages": allowed,
        "deferred_pages": deferred,
    }


def page_range(start: int, count: int) -> str:
    """把从 1 开始的页码区间格式化成 ``1-4`` 这样的可核对范围。"""
    if count <= 0:
        return ""
    if count == 1:
        return str(start)
    return f"{start}-{start + count - 1}"
