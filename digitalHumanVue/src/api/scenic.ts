/**
 * 管理端景区结构化数据 API 封装（对齐后端 /api/admin/scenic 契约，
 * 权威文档见 digitalHumanApi/doc/scenic-data-api.md）。
 *
 * 数据源为 app/data/spots.json 与 routes.json——导览 agent 结构化工具
 * （search_spots/search_routes 等）与首页活数据的单一来源：管理端写入后
 * agent 工具/首页实时生效（页面数据 = agent 数据）。
 * 鉴权：管理员 JWT（Authorization Bearer）或 X-Admin-Token（向后兼容），
 * 见 utils/adminHeaders.ts；删除类操作要求 admin 角色。
 */
import { API_ADMIN_SCENIC_SPOTS, API_ADMIN_SCENIC_ROUTES } from '@/api'
import type { ScenicRoute, ScenicSpot } from '@/types/scenic'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { adminApiHeaders } from '@/utils/adminHeaders'

function headers(json = true): Record<string, string> {
  return adminApiHeaders(json)
}

// ── 景点 ────────────────────────────────────────────────────────────────

/** 景点列表（完整返回；当前数据量小，分页由前端承担） */
export function fetchScenicSpots(): Promise<ScenicSpot[]> {
  return fetchWithCodeEnvelope<ScenicSpot[]>(API_ADMIN_SCENIC_SPOTS, {
    headers: headers(false),
  })
}

/** 新建景点（id 由服务端自动生成；名称重复 422） */
export function createScenicSpot(payload: Omit<ScenicSpot, 'id'>): Promise<ScenicSpot> {
  return fetchWithCodeEnvelope<ScenicSpot>(API_ADMIN_SCENIC_SPOTS, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(payload),
  })
}

/** 部分更新景点（仅更新传入字段；不存在 404） */
export function updateScenicSpot(
  spotId: string,
  payload: Partial<Omit<ScenicSpot, 'id'>>,
): Promise<ScenicSpot> {
  return fetchWithCodeEnvelope<ScenicSpot>(`${API_ADMIN_SCENIC_SPOTS}/${spotId}`, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify(payload),
  })
}

/** 删除景点（删除类敏感操作，要求 admin 角色；operator 403） */
export function deleteScenicSpot(spotId: string): Promise<{ success: boolean }> {
  return fetchWithCodeEnvelope<{ success: boolean }>(
    `${API_ADMIN_SCENIC_SPOTS}/${spotId}`,
    { method: 'DELETE', headers: headers(false) },
  )
}

// ── 路线 ────────────────────────────────────────────────────────────────

/** 路线列表（spot_ids 引用景点 id） */
export function fetchScenicRoutes(): Promise<ScenicRoute[]> {
  return fetchWithCodeEnvelope<ScenicRoute[]>(API_ADMIN_SCENIC_ROUTES, {
    headers: headers(false),
  })
}

/** 新建路线（spot_ids 须引用已存在景点 id；未知 id 422） */
export function createScenicRoute(
  payload: Omit<ScenicRoute, 'id'>,
): Promise<ScenicRoute> {
  return fetchWithCodeEnvelope<ScenicRoute>(API_ADMIN_SCENIC_ROUTES, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(payload),
  })
}

/** 部分更新路线（仅更新传入字段；不存在 404） */
export function updateScenicRoute(
  routeId: string,
  payload: Partial<Omit<ScenicRoute, 'id'>>,
): Promise<ScenicRoute> {
  return fetchWithCodeEnvelope<ScenicRoute>(`${API_ADMIN_SCENIC_ROUTES}/${routeId}`, {
    method: 'PUT',
    headers: headers(),
    body: JSON.stringify(payload),
  })
}

/** 删除路线（删除类敏感操作，要求 admin 角色；operator 403） */
export function deleteScenicRoute(routeId: string): Promise<{ success: boolean }> {
  return fetchWithCodeEnvelope<{ success: boolean }>(
    `${API_ADMIN_SCENIC_ROUTES}/${routeId}`,
    { method: 'DELETE', headers: headers(false) },
  )
}
