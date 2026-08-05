/**
 * 用户认证相关类型（对齐后端 PR #14 接口契约 digitalHumanApi/app/schemas/auth.py）。
 * API 响应字段保持 snake_case（对齐后端）。
 */

/** 注册用户信息（绝不包含 password 等敏感字段）。 */
export interface UserInfo {
  id: number
  username: string
  nickname: string | null
  created_at: string
  last_login_at: string | null
}

/** 登录成功响应：JWT + 用户信息。 */
export interface AuthResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: UserInfo
}

/** 游客 token 签发响应（匿名，无持久化用户记录）。 */
export interface GuestResponse {
  access_token: string
  token_type: string
  expires_in: number
  guest_id: string
}

/** /auth/me 对游客 token 的返回。 */
export interface GuestIdentity {
  id: string
  is_guest: boolean
}

/** /auth/me 的返回：登录用户信息或游客身份。 */
export type MeResponse = UserInfo | GuestIdentity

/** 登录请求体。 */
export interface LoginPayload {
  username: string
  password: string
}

/** 注册请求体（nickname 可选）。 */
export interface RegisterPayload {
  username: string
  password: string
  nickname?: string
}
