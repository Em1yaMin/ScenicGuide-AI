"""景区结构化数据管理 API（`/api/admin/scenic`，X-Admin-Token 鉴权）。

管理导览 agent 的结构化景区数据（spots/routes）——数据源为
`app/data/spots.json` 与 `routes.json`，与 agent 查询工具（search_spots /
search_routes 等）与首页活数据（home_service）为**同一份数据**：管理端
写文件后 agent 工具/首页零改动实时生效（单一来源 = JSON 文件）。

鉴权与 RBAC（批次6 C3 安全加固同源）：
- 查询/新增/修改：`verify_admin_token`（operator 可做，与知识库增改一致）
- 删除类敏感操作：`require_role("admin")`（与知识库删除/记忆删除一致）

权威契约文档：`doc/scenic-data-api.md`。
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.routers.deps import require_role, verify_admin_token
from app.schemas.scenic import (
    ScenicRoute,
    ScenicRouteCreate,
    ScenicRouteUpdate,
    ScenicSpot,
    ScenicSpotCreate,
    ScenicSpotUpdate,
)
from app.services import scenic_data_service

router = APIRouter(prefix="/api/admin/scenic", tags=["景区数据管理"])


def _value_error_422(exc: ValueError) -> HTTPException:
    """业务校验失败（名称重复/未知景点 id/难度非法）统一 422。"""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(exc),
    )


# ── 景点（spots） ────────────────────────────────────────────────────────


@router.get("/spots", response_model=List[ScenicSpot])
async def list_spots(
    _: str = Depends(verify_admin_token),
) -> List[ScenicSpot]:
    """景点列表（完整返回；当前数据量小，分页由前端承担）。"""
    return scenic_data_service.list_spots()


@router.get("/spots/{spot_id}", response_model=ScenicSpot)
async def get_spot(
    spot_id: str,
    _: str = Depends(verify_admin_token),
) -> ScenicSpot:
    """按 id 查询景点。"""
    spot = scenic_data_service.get_spot(spot_id)
    if spot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"景点不存在: {spot_id}",
        )
    return spot


@router.post("/spots", response_model=ScenicSpot, status_code=status.HTTP_201_CREATED)
async def create_spot(
    request: ScenicSpotCreate,
    _: str = Depends(verify_admin_token),
) -> ScenicSpot:
    """新建景点（id 服务端自动生成；名称唯一校验）。"""
    try:
        return scenic_data_service.create_spot(request)
    except ValueError as exc:
        raise _value_error_422(exc) from exc


@router.put("/spots/{spot_id}", response_model=ScenicSpot)
async def update_spot(
    spot_id: str,
    request: ScenicSpotUpdate,
    _: str = Depends(verify_admin_token),
) -> ScenicSpot:
    """部分更新景点（仅更新传入的非 None 字段）。"""
    try:
        spot = scenic_data_service.update_spot(spot_id, request)
    except ValueError as exc:
        raise _value_error_422(exc) from exc
    if spot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"景点不存在: {spot_id}",
        )
    return spot


@router.delete("/spots/{spot_id}")
async def delete_spot(
    spot_id: str,
    _: str = Depends(require_role("admin")),
) -> dict[str, bool]:
    """删除景点（删除类敏感操作，要求 admin 角色）。"""
    deleted = scenic_data_service.delete_spot(spot_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"景点不存在: {spot_id}",
        )
    return {"success": True}


# ── 路线（routes） ───────────────────────────────────────────────────────


@router.get("/routes", response_model=List[ScenicRoute])
async def list_routes(
    _: str = Depends(verify_admin_token),
) -> List[ScenicRoute]:
    """路线列表（完整返回；spot_ids 引用景点 id）。"""
    return scenic_data_service.list_routes()


@router.get("/routes/{route_id}", response_model=ScenicRoute)
async def get_route(
    route_id: str,
    _: str = Depends(verify_admin_token),
) -> ScenicRoute:
    """按 id 查询路线。"""
    route = scenic_data_service.get_route(route_id)
    if route is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"路线不存在: {route_id}",
        )
    return route


@router.post("/routes", response_model=ScenicRoute, status_code=status.HTTP_201_CREATED)
async def create_route(
    request: ScenicRouteCreate,
    _: str = Depends(verify_admin_token),
) -> ScenicRoute:
    """新建路线（id 服务端自动生成；名称唯一 + spot_ids 引用校验）。"""
    try:
        return scenic_data_service.create_route(request)
    except ValueError as exc:
        raise _value_error_422(exc) from exc


@router.put("/routes/{route_id}", response_model=ScenicRoute)
async def update_route(
    route_id: str,
    request: ScenicRouteUpdate,
    _: str = Depends(verify_admin_token),
) -> ScenicRoute:
    """部分更新路线（仅更新传入的非 None 字段）。"""
    try:
        route = scenic_data_service.update_route(route_id, request)
    except ValueError as exc:
        raise _value_error_422(exc) from exc
    if route is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"路线不存在: {route_id}",
        )
    return route


@router.delete("/routes/{route_id}")
async def delete_route(
    route_id: str,
    _: str = Depends(require_role("admin")),
) -> dict[str, bool]:
    """删除路线（删除类敏感操作，要求 admin 角色）。"""
    deleted = scenic_data_service.delete_route(route_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"路线不存在: {route_id}",
        )
    return {"success": True}
