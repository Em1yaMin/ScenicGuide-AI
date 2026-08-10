/**
 * 管理端记忆管理 API 封装（对齐后端 PR #49 /api/admin/memories 契约，
 * 权威文档见 digitalHumanApi/doc/memory-admin-api.md）。
 * 鉴权：管理员 JWT（Authorization Bearer，批次9 账号密码登录）或
 * X-Admin-Token（localStorage admin_api_token，向后兼容），见 utils/adminHeaders.ts。
 */
import { API_ADMIN_MEMORIES, API_ADMIN_MEMORIES_USERS } from '@/api'
import type {
  AdminMemoryCreatePayload,
  AdminMemoryDeleteResponse,
  AdminMemoryItem,
  AdminMemoryListResponse,
  AdminMemoryUpdatePayload,
  AdminUserListResponse,
} from '@/types/memory'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { adminApiHeaders } from '@/utils/adminHeaders'


/** 用户列表（分页；keyword 模糊搜索用户名/昵称） */
export function fetchMemoryUsers(params: {
  keyword?: string
  page?: number
  page_size?: number
}): Promise<AdminUserListResponse> {
  const qs = new URLSearchParams()
  if (params.keyword) qs.append('keyword', params.keyword)
  qs.append('page', String(params.page ?? 1))
  qs.append('page_size', String(params.page_size ?? 20))
  return fetchWithCodeEnvelope<AdminUserListResponse>(
    `${API_ADMIN_MEMORIES_USERS}?${qs.toString()}`,
    { headers: adminApiHeaders(false) },
  )
}

/** 按用户查看记忆列表（游客/匿名用户返回空列表） */
export function fetchUserMemories(user: string): Promise<AdminMemoryListResponse> {
  const qs = new URLSearchParams({ user })
  return fetchWithCodeEnvelope<AdminMemoryListResponse>(`${API_ADMIN_MEMORIES}?${qs.toString()}`, {
    headers: adminApiHeaders(false),
  })
}

/** 新增记忆（目标用户须为注册用户，游客/匿名 400） */
export function createMemory(payload: AdminMemoryCreatePayload): Promise<AdminMemoryItem> {
  return fetchWithCodeEnvelope<AdminMemoryItem>(API_ADMIN_MEMORIES, {
    method: 'POST',
    headers: adminApiHeaders(),
    body: JSON.stringify(payload),
  })
}

/** 修改记忆（删除后重建，memory_id 会更新为新建 uuid） */
export function updateMemory(
  memoryId: string,
  payload: AdminMemoryUpdatePayload,
): Promise<AdminMemoryItem> {
  return fetchWithCodeEnvelope<AdminMemoryItem>(
    `${API_ADMIN_MEMORIES}/${encodeURIComponent(memoryId)}`,
    {
      method: 'PUT',
      headers: adminApiHeaders(),
      body: JSON.stringify(payload),
    },
  )
}

/** 删除记忆（跨用户，管理员例外；不可恢复） */
export function deleteMemory(memoryId: string): Promise<AdminMemoryDeleteResponse> {
  return fetchWithCodeEnvelope<AdminMemoryDeleteResponse>(
    `${API_ADMIN_MEMORIES}/${encodeURIComponent(memoryId)}`,
    { method: 'DELETE', headers: adminApiHeaders(false) },
  )
}
