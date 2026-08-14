/**
 * 管理端账号管理 API 封装（对齐后端 /api/admin/users 契约）。
 * 鉴权：管理员 JWT（Authorization Bearer）或 X-Admin-Token（向后兼容），
 * 见 utils/adminHeaders.ts。
 */
import { API_ADMIN_USERS, API_ADMIN_USERS_AUDIT_LOGS } from '@/api'
import type {
  AdminAuditLogListResponse,
  AdminUserCreatePayload,
  AdminUserDeleteResponse,
  AdminUserItem,
  AdminUserListResponse,
  AdminUserResetPasswordPayload,
  AdminUserResetPasswordResponse,
  AdminUserSetDisabledPayload,
} from '@/types/userAdmin'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { adminApiHeaders } from '@/utils/adminHeaders'


/** 账号分页列表（keyword 模糊搜索用户名/昵称；含对话/记忆条数概览） */
export function fetchAdminUsers(params: {
  keyword?: string
  page?: number
  page_size?: number
}): Promise<AdminUserListResponse> {
  const qs = new URLSearchParams()
  if (params.keyword) qs.append('keyword', params.keyword)
  qs.append('page', String(params.page ?? 1))
  qs.append('page_size', String(params.page_size ?? 20))
  return fetchWithCodeEnvelope<AdminUserListResponse>(
    `${API_ADMIN_USERS}?${qs.toString()}`,
    { headers: adminApiHeaders(false) },
  )
}

/** 账号详情（含对话/记忆条数概览） */
export function fetchAdminUserDetail(userId: number): Promise<AdminUserItem> {
  return fetchWithCodeEnvelope<AdminUserItem>(
    `${API_ADMIN_USERS}/${userId}`,
    { headers: adminApiHeaders(false) },
  )
}

/** 创建账号（用户名重复 409；密码强度不足 422） */
export function createAdminUser(payload: AdminUserCreatePayload): Promise<AdminUserItem> {
  return fetchWithCodeEnvelope<AdminUserItem>(API_ADMIN_USERS, {
    method: 'POST',
    headers: adminApiHeaders(),
    body: JSON.stringify(payload),
  })
}

/** 禁用/启用账号（软禁用：登录/对话受控；超管账号 400） */
export function setAdminUserDisabled(
  userId: number,
  payload: AdminUserSetDisabledPayload,
): Promise<AdminUserItem> {
  return fetchWithCodeEnvelope<AdminUserItem>(
    `${API_ADMIN_USERS}/${userId}/disabled`,
    {
      method: 'PUT',
      headers: adminApiHeaders(),
      body: JSON.stringify(payload),
    },
  )
}

/** 重置密码（指定新密码或生成临时密码——临时密码仅本次响应返回） */
export function resetAdminUserPassword(
  userId: number,
  payload: AdminUserResetPasswordPayload = {},
): Promise<AdminUserResetPasswordResponse> {
  return fetchWithCodeEnvelope<AdminUserResetPasswordResponse>(
    `${API_ADMIN_USERS}/${userId}/reset-password`,
    {
      method: 'POST',
      headers: adminApiHeaders(),
      body: JSON.stringify(payload),
    },
  )
}

/** 删除账号（单目标；级联清理对话/评估/记忆，影响如实汇报；不可恢复） */
export function deleteAdminUser(userId: number): Promise<AdminUserDeleteResponse> {
  return fetchWithCodeEnvelope<AdminUserDeleteResponse>(
    `${API_ADMIN_USERS}/${userId}`,
    { method: 'DELETE', headers: adminApiHeaders(false) },
  )
}

/** 账号管理操作审计日志（分页/关键词/动作筛选） */
export function fetchAdminAuditLogs(params: {
  keyword?: string
  action?: string
  page?: number
  page_size?: number
}): Promise<AdminAuditLogListResponse> {
  const qs = new URLSearchParams()
  if (params.keyword) qs.append('keyword', params.keyword)
  if (params.action) qs.append('action', params.action)
  qs.append('page', String(params.page ?? 1))
  qs.append('page_size', String(params.page_size ?? 20))
  return fetchWithCodeEnvelope<AdminAuditLogListResponse>(
    `${API_ADMIN_USERS_AUDIT_LOGS}?${qs.toString()}`,
    { headers: adminApiHeaders(false) },
  )
}
