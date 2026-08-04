/**
 * 用户认证 API 封装（对齐后端 PR #14 /api/v1/auth/* 契约）。
 * 统一走 fetchWithCodeEnvelope：HTTP 层错误（401/409/422）会携带后端 detail 消息抛出。
 */
import {
  API_AUTH_GUEST,
  API_AUTH_LOGIN,
  API_AUTH_ME,
  API_AUTH_REGISTER,
} from '@/api'
import type {
  AuthResponse,
  GuestResponse,
  LoginPayload,
  MeResponse,
  RegisterPayload,
  UserInfo,
} from '@/types/auth'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

function jsonHeaders(): Record<string, string> {
  return { 'Content-Type': 'application/json' }
}

/** 注册新用户。成功返回用户信息（不含 token，需另行登录）。 */
export function registerUser(payload: RegisterPayload): Promise<UserInfo> {
  return fetchWithCodeEnvelope<UserInfo>(API_AUTH_REGISTER, {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  })
}

/** 登录：校验凭据，返回 JWT + 用户信息。凭据错误后端返回 401。 */
export function loginUser(payload: LoginPayload): Promise<AuthResponse> {
  return fetchWithCodeEnvelope<AuthResponse>(API_AUTH_LOGIN, {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  })
}

/** 获取当前用户信息（需 Bearer token；无效 token 后端返回 401）。 */
export function fetchMe(token: string): Promise<MeResponse> {
  return fetchWithCodeEnvelope<MeResponse>(API_AUTH_ME, {
    headers: { Authorization: `Bearer ${token}` },
  })
}

/** 签发短期游客 token（匿名，无持久化用户记录）。 */
export function createGuestToken(): Promise<GuestResponse> {
  return fetchWithCodeEnvelope<GuestResponse>(API_AUTH_GUEST, {
    method: 'POST',
  })
}
