"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import resource
from app.routers import chains
from app.routers import rate_admission

# 限速准入使用固定路径 /api/rate-admission/...，必须排在通用资源路由
# /api/{module}/{entry_id} 之前，否则会被动态路径按 entry_id 抢先匹配。
ROUTERS = [rate_admission, resource, chains]
