/**
 * 管理端账号管理类型（对齐后端 /api/admin/users 契约，
 * 后端权威 schema 见 digitalHumanApi/app/schemas/user_admin.py）。
 */

/** 账号列表条目 / 详情（含对话/记忆条数概览） */
export interface AdminUserItem {
  id: number
  username: string
  nickname: string | null
  is_disabled: boolean // 软禁用标记（禁用后登录/对话受控）
  disabled_at: string | null
  dialogue_count: number // 关联对话条数
  memory_count: number // 长期记忆条数
  created_at: string
  last_login_at: string | null
}

/** 账号分页列表响应 */
export interface AdminUserListResponse {
  items: AdminUserItem[]
  total: number
  page: number
  page_size: number
}

/** 创建账号请求体 */
export interface AdminUserCreatePayload {
  username: string // 3-32 位字母/数字/下划线
  password: string // 至少 6 位
  nickname?: string | null
}

/** 禁用/启用请求体 */
export interface AdminUserSetDisabledPayload {
  disabled: boolean
}

/** 重置密码请求体（new_password 留空则由系统生成临时密码） */
export interface AdminUserResetPasswordPayload {
  new_password?: string
}

/** 重置密码响应（临时密码仅本次返回，请立即保存） */
export interface AdminUserResetPasswordResponse {
  user_id: number
  username: string
  generated: boolean // true=系统生成临时密码
  new_password: string
}

/** 删除账号响应（级联清理影响如实汇报） */
export interface AdminUserDeleteResponse {
  success: boolean
  user_id: number
  username: string
  deleted_dialogues: number
  deleted_evaluations: number
  deleted_memories: number
}

/** 账号管理操作审计动作取值 */
export type AdminAuditAction =
  | 'create'
  | 'disable'
  | 'enable'
  | 'reset_password'
  | 'delete'

/** 审计动作展示文案 */
export const ADMIN_AUDIT_ACTION_LABELS: Record<AdminAuditAction, string> = {
  create: '创建账号',
  disable: '禁用账号',
  enable: '启用账号',
  reset_password: '重置密码',
  delete: '删除账号',
}

/** 审计日志条目 */
export interface AdminAuditLogItem {
  id: number
  operator: string // 操作人标识（管理员账号 / admin-agent）
  action: AdminAuditAction
  target_user_id: number | null
  target_username: string // 目标用户名快照（用户删除后仍可查）
  detail: string | null // 结果摘要
  created_at: string
}

/** 审计日志分页列表响应 */
export interface AdminAuditLogListResponse {
  items: AdminAuditLogItem[]
  total: number
  page: number
  page_size: number
}
