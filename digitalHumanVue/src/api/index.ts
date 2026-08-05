/**
 * 集中式 API 端点路径常量。
 * 所有组件的 fetch 调用改为引用此模块，避免硬编码 URL 字面量散落各处。
 */
export const API_VISITOR_REGISTER = '/api/v1/visitor/register'
export const API_ADMIN_DASHBOARD = '/api/v1/admin/dashboard'
export const API_ADMIN_REPORT = '/api/v1/admin/report'
export const API_ADMIN_KNOWLEDGE = '/api/admin/knowledge'
export const API_ADMIN_KNOWLEDGE_IMPORT = '/api/admin/knowledge/import'
export const API_DIALOGUE_SYNC_NOW = '/api/v1/dialogue/sync-now'
export const API_DIALOGUE_CHAT = '/api/v1/dialogue/chat'
export const API_ANALYSIS_RUN_NOW = '/api/v1/analysis/run-now'
export const API_ANALYSIS_OVERVIEW = '/api/v1/analysis/overview'
export const API_ANALYSIS_TRENDS = '/api/v1/analysis/trends'
export const API_VISITOR_RECOMMENDED_QUESTIONS = '/api/v1/visitor/recommended-questions'
export const API_AUTH_REGISTER = '/api/v1/auth/register'
export const API_AUTH_LOGIN = '/api/v1/auth/login'
export const API_AUTH_ME = '/api/v1/auth/me'
export const API_AUTH_GUEST = '/api/v1/auth/guest'
