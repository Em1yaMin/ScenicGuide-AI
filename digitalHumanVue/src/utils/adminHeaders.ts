/**
 * 管理端 API 请求头构造（批次9 统一入口）。
 *
 * 双凭据兼容：优先携带管理员 JWT（Authorization: Bearer，账号密码登录签发）；
 * 同时保留 X-Admin-Token（localStorage admin_api_token，向后兼容旧用法——
 * 内部调用/脚本/既有部署不受影响）。后端 deps.verify_admin_token 双路径任一
 * 命中即放行（批次9 起）。
 */

const LEGACY_TOKEN_KEY = 'admin_api_token'
const ADMIN_JWT_KEY = 'admin_jwt'

/** 构造管理端 API 请求头：携带管理员 JWT 与既有 X-Admin-Token（有则带）。 */
export function adminApiHeaders(json = true): Record<string, string> {
  const headers: Record<string, string> = {}
  const jwt = localStorage.getItem(ADMIN_JWT_KEY)
  const legacy = localStorage.getItem(LEGACY_TOKEN_KEY)
  if (jwt) headers['Authorization'] = `Bearer ${jwt}`
  if (legacy) headers['X-Admin-Token'] = legacy
  if (json) headers['Content-Type'] = 'application/json'
  return headers
}
