/** 景区结构化数据（/api/admin/scenic，导览 agent 景点/路线数据单一来源）。
 * 权威契约文档见 digitalHumanApi/doc/scenic-data-api.md。 */

/** 景点（字段与 app/data/spots.json 对齐——agent 工具 search_spots 数据源） */
export interface ScenicSpot {
  id: string
  name: string
  description: string
  tags: string[]
  duration_minutes: number
  difficulty: string
  location: string
  highlights: string[]
}

/** 新建景点请求（id 由服务端自动生成） */
export interface ScenicSpotCreatePayload {
  name: string
  description: string
  tags: string[]
  duration_minutes: number
  difficulty: string
  location: string
  highlights: string[]
}

/** 路线（字段与 app/data/routes.json 对齐——agent 工具 search_routes 数据源） */
export interface ScenicRoute {
  id: string
  name: string
  description: string
  spot_ids: string[]
  tags: string[]
  suitable_for: string[]
  total_duration_minutes: number
  difficulty: string
  recommend_reason: string
}

/** 新建路线请求（spot_ids 须引用已存在景点 id） */
export interface ScenicRouteCreatePayload {
  name: string
  description: string
  spot_ids: string[]
  tags: string[]
  suitable_for: string[]
  total_duration_minutes: number
  difficulty: string
  recommend_reason: string
}

/** 难度可选值（与后端约定/既有数据一致） */
export const SCENIC_DIFFICULTIES = ['轻松', '中等', '挑战'] as const
