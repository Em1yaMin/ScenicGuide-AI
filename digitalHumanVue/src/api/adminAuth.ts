/**
 * 管理端登录 API 封装（批次9：管理员账号密码登录，替代 X-Admin-Token 输入形式）。
 * 契约：POST /api/v1/admin/login → { access_token, token_type, expires_in, username }
 * 统一走 fetchWithCodeEnvelope：HTTP 层错误（401 用户名或密码错误）携带 detail 抛出。
 */
import { API_ADMIN_LOGIN } from '@/api'
import type { AdminLoginPayload, AdminLoginResponse } from '@/types/adminAuth'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

/** 管理员账号密码登录：成功返回管理员 JWT + 账号。 */
export function adminLogin(payload: AdminLoginPayload): Promise<AdminLoginResponse> {
  return fetchWithCodeEnvelope<AdminLoginResponse>(API_ADMIN_LOGIN, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
