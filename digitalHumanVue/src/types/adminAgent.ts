/**
 * 管理端管理员 Agent 类型定义（对齐后端 PR #51 /api/admin/agent 契约，
 * 权威实现见 digitalHumanApi/app/routers/admin_agent.py）。
 */

/** 非流式对话响应（POST /api/admin/agent/chat） */
export interface AdminAgentChatResponse {
  answer: string
  conversation_id: string
}

/** SSE step 事件：工具调用步骤（🔍 步骤气泡数据源） */
export interface AdminAgentStepEvent {
  type: 'step'
  tool: string
  label: string
}

/** SSE token 事件：回答增量（逐字渲染） */
export interface AdminAgentTokenEvent {
  type: 'token'
  text: string
}

/** SSE done 事件：流式完成（携带会话 ID，用于多轮连续性） */
export interface AdminAgentDoneEvent {
  type: 'done'
  conversation_id: string
}

/** SSE error 事件：链路错误（管理端无 Dify 兜底，fallback 恒为 false） */
export interface AdminAgentErrorEvent {
  type: 'error'
  message: string
  fallback: boolean
}

/** SSE 事件联合类型（与用户侧对话契约一致：step/token/done/error） */
export type AdminAgentStreamEvent =
  | AdminAgentStepEvent
  | AdminAgentTokenEvent
  | AdminAgentDoneEvent
  | AdminAgentErrorEvent
