/**
 * 管理端记忆管理类型（对齐后端 PR #49 /api/admin/memories 契约，
 * 权威文档见 digitalHumanApi/doc/memory-admin-api.md）。
 */

/** 记忆事实类型：preference=偏好 / fact=事实 / habit=习惯 */
export type MemoryFactType = 'preference' | 'fact' | 'habit'

/** 管理端记忆条目（memory_id 为新条目 uuid；legacy 条目为派生 id） */
export interface AdminMemoryItem {
  memory_id: string
  user: string // 归属用户标识（注册用户为 str(id)）
  fact_type: MemoryFactType
  content: string // 自然语言记忆句（1-200 字符）
  source_conversation_id: string // 来源会话 ID（溯源用，可为空串）
  timestamp: number // 提取/更新时间（Unix 秒）
}

/** 按用户查看记忆列表响应（游客返回空列表） */
export interface AdminMemoryListResponse {
  user: string
  total: number
  items: AdminMemoryItem[]
}

/** 新增记忆请求体 */
export interface AdminMemoryCreatePayload {
  user: string // 目标注册用户 id（字符串形式）
  fact_type: MemoryFactType
  content: string
}

/** 修改记忆请求体（部分更新，content 与 fact_type 至少提供其一） */
export interface AdminMemoryUpdatePayload {
  fact_type?: MemoryFactType
  content?: string
}

/** 删除记忆响应 */
export interface AdminMemoryDeleteResponse {
  success: boolean
  user: string
  memory_id: string
  deleted: number
}

/** 管理端用户列表条目（记忆管理页选择用户用） */
export interface AdminUserItem {
  id: number
  username: string
  nickname: string | null
  memory_count: number // 该用户长期记忆条数（实时统计）
  created_at: string
  last_login_at: string | null
}

/** 管理端用户分页列表响应 */
export interface AdminUserListResponse {
  items: AdminUserItem[]
  total: number
  page: number
  page_size: number
}

/** 记忆表单数据（新增/编辑共用） */
export interface MemoryFormData {
  fact_type: MemoryFactType
  content: string
}
