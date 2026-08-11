/**
 * 管理员登录相关类型（批次9：对齐后端 app/schemas/admin.py AdminLoginRequest/Response）。
 * 字段保持 snake_case（对齐后端）。
 */

/** 管理员登录请求体（账号密码来自环境变量 ADMIN_USERNAME / ADMIN_PASSWORD）。 */
export interface AdminLoginPayload {
  username: string
  password: string
}

/** 管理员登录成功响应：JWT（typ=admin）+ 账号。 */
export interface AdminLoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  username: string
}
