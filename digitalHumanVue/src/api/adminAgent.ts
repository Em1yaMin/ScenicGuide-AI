/**
 * 管理端管理员 Agent API 封装（对齐后端 PR #51 /api/admin/agent 契约）。
 *
 * - `chatWithAdminAgent`：非流式对话（备用：流式链路异常时兜底）
 * - `streamAdminAgentChat`：SSE 流式对话（主用），事件契约与用户侧一致
 *   （step/token/done/error），解析模式参考 ADH langchainAgent 的 SSE 消费：
 *   帧格式 `data: {json}\n\n`，按行取 `data:` 前缀解析 JSON 后分发回调。
 *
 * 全部接口需 X-Admin-Token 鉴权（token 存 localStorage 的 admin_api_token，
 * 与知识库/公告/记忆管理页一致）。
 */
import { API_ADMIN_AGENT_CHAT, API_ADMIN_AGENT_CHAT_STREAM } from '@/api'
import type {
  AdminAgentChatResponse,
  AdminAgentDoneEvent,
  AdminAgentErrorEvent,
  AdminAgentStepEvent,
  AdminAgentTokenEvent,
} from '@/types/adminAgent'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

const TOKEN_KEY = 'admin_api_token'

/** 构造带 X-Admin-Token 的请求头（json=true 时附带 Content-Type） */
function adminHeaders(json = true): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Admin-Token': localStorage.getItem(TOKEN_KEY) ?? '',
  }
  if (json) headers['Content-Type'] = 'application/json'
  return headers
}

/** 对话请求体（conversation_id 透传回显，空串由后端生成新 uuid） */
export interface AdminAgentChatPayload {
  query: string
  conversation_id?: string
}

/** SSE 流式事件回调集（页面按需订阅） */
export interface AdminAgentStreamHandlers {
  onStep?: (event: AdminAgentStepEvent) => void
  onToken?: (event: AdminAgentTokenEvent) => void
  onDone?: (event: AdminAgentDoneEvent) => void
  onError?: (event: AdminAgentErrorEvent) => void
}

/** 非流式对话（备用路径）：返回 { answer, conversation_id } */
export async function chatWithAdminAgent(
  query: string,
  conversationId = '',
): Promise<AdminAgentChatResponse> {
  // 管理 agent 工具调用链可能较长，禁用默认 15s 超时
  return fetchWithCodeEnvelope<AdminAgentChatResponse>(API_ADMIN_AGENT_CHAT, {
    method: 'POST',
    headers: adminHeaders(),
    body: JSON.stringify({ query, conversation_id: conversationId } satisfies AdminAgentChatPayload),
    timeoutMs: 0,
  })
}

/**
 * SSE 流式对话：读取响应流并逐帧分发事件回调。
 *
 * 解析细节：
 * - fetch 流经 TextDecoder(stream) 增量解码，统一剔除 \r 后按 `\n\n` 分帧
 *   （兼容 `\r\n` 换行，防分块边界切碎帧）
 * - 每帧按行取 `data:` 前缀后的 JSON，解析失败/未知事件类型静默跳过
 * - HTTP 非 2xx：提取 FastAPI { detail } 抛出 FetchError（含 401 供页面引导设置 Token）
 *
 * @param payload 对话请求体（query + conversation_id）
 * @param handlers 事件回调（step/token/done/error）
 * @param signal 可选的 AbortSignal（页面停止生成时中断流）
 */
export async function streamAdminAgentChat(
  payload: AdminAgentChatPayload,
  handlers: AdminAgentStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(API_ADMIN_AGENT_CHAT_STREAM, {
      method: 'POST',
      headers: adminHeaders(),
      body: JSON.stringify(payload),
      signal,
    })
  } catch (e) {
    throw new Error(`网络请求失败: ${e instanceof Error ? e.message : e}`)
  }

  if (!response.ok) {
    throw await buildStreamError(response)
  }
  if (!response.body) {
    throw new Error('浏览器不支持流式响应（缺少 response.body）')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      // 统一 \r\n → \n，保证帧边界可被 \n\n 正确切分
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, '')
      let sep = buffer.indexOf('\n\n')
      while (sep !== -1) {
        dispatchFrame(buffer.slice(0, sep), handlers)
        buffer = buffer.slice(sep + 2)
        sep = buffer.indexOf('\n\n')
      }
    }
    // 末尾残余帧（流未以空行结尾）
    if (buffer.trim()) dispatchFrame(buffer, handlers)
  } finally {
    reader.releaseLock()
  }
}

/** 解析单帧 SSE（可能含多行 data:），逐条分发事件回调 */
function dispatchFrame(frame: string, handlers: AdminAgentStreamHandlers): void {
  for (const line of frame.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('data:')) continue
    const data = trimmed.slice('data:'.length).trim()
    if (!data) continue
    let event: unknown
    try {
      event = JSON.parse(data)
    } catch {
      continue // 非 JSON 帧（如心跳注释）静默跳过
    }
    if (typeof event !== 'object' || event === null) return
    const e = event as Record<string, unknown>
    switch (e.type) {
      case 'step':
        handlers.onStep?.({
          type: 'step',
          tool: String(e.tool ?? ''),
          label: String(e.label ?? ''),
        })
        break
      case 'token':
        handlers.onToken?.({ type: 'token', text: String(e.text ?? '') })
        break
      case 'done':
        handlers.onDone?.({
          type: 'done',
          conversation_id: String(e.conversation_id ?? ''),
        })
        break
      case 'error':
        handlers.onError?.({
          type: 'error',
          message: String(e.message ?? '管理员 Agent 服务暂时不可用'),
          fallback: Boolean(e.fallback),
        })
        break
      default:
        break // 未知事件类型忽略（保持向前兼容）
    }
  }
}

/** 从非 OK 响应中提取错误消息（FastAPI 的 { detail }），返回 FetchError */
async function buildStreamError(response: Response): Promise<Error> {
  let detail = ''
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') detail = body.detail
  } catch {
    // 非 JSON 错误体（如代理层 502）忽略，用通用消息
  }
  const message = detail || `服务器返回错误 (${response.status})`
  const err = new Error(message) as Error & { httpStatus?: number }
  err.httpStatus = response.status
  return err
}
