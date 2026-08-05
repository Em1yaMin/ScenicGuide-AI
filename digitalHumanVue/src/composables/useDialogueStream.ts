/**
 * 对话 SSE 流式消费 composable（阶段1 T3）。
 *
 * 消费 POST /api/v1/dialogue/chat（streaming=true）返回的 SSE 事件流：
 *
 * - `step`：agent 工具调用步骤（正在检索知识库 / 查询景点 / 推荐路线…），
 *   用于思考过程可视化
 * - `token`：最终回答增量，逐字拼接到当前回答（逐字显示）
 * - `done`：流式完成（conversation_id 持久化，供多轮对话续接）
 * - `error`：链路错误；`fallback=true` 表示主链路异常已切换 Dify 兜底
 *   （清空部分回答与步骤，继续接收兜底输出）；`fallback=false` 表示
 *   双链路均失败，展示错误文案
 *
 * 使用 fetch + ReadableStream 逐帧解析（不引入 SSE 库，无额外依赖）。
 */
import { ref } from 'vue'
import { API_DIALOGUE_CHAT } from '@/api'
import { useAuthStore } from '@/stores/auth'

/** agent 步骤事件（思考过程可视化用）。 */
export interface StreamStep {
  tool: string
  label: string
}

/** 单条对话消息（用户 / 助手）。 */
export interface ChatTurn {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 已展示的步骤（仅助手消息）。 */
  steps: StreamStep[]
  /** 进行中的步骤文案（仅助手消息，流式期间展示 spinner）。 */
  currentStep: string | null
  /** 主链路异常已切换备用服务（仅助手消息）。 */
  fallback: boolean
  /** 错误文案（仅助手消息，双链路均失败时）。 */
  error: string | null
  /** 是否仍在流式接收中。 */
  streaming: boolean
}

let turnSeq = 0

function newTurn(role: 'user' | 'assistant', content = ''): ChatTurn {
  turnSeq += 1
  return {
    id: `turn-${Date.now()}-${turnSeq}`,
    role,
    content,
    steps: [],
    currentStep: null,
    fallback: false,
    error: null,
    streaming: role === 'assistant',
  }
}

export function useDialogueStream() {
  const auth = useAuthStore()
  const turns = ref<ChatTurn[]>([])
  const isStreaming = ref(false)
  const conversationId = ref('')
  const fatalError = ref<string | null>(null)

  let abortController: AbortController | null = null

  /** 取当前助手消息（发送新消息时创建占位，流式期间复用）。 */
  function currentAssistant(): ChatTurn {
    const last = turns.value[turns.value.length - 1]
    if (last && last.role === 'assistant' && last.streaming) return last
    const turn = newTurn('assistant')
    turns.value.push(turn)
    return turn
  }

  /** 解析单个 SSE data 帧并应用到当前助手消息。 */
  function handleFrame(frame: string, assistant: ChatTurn) {
    if (!frame.startsWith('data:')) return
    const payload = frame.slice(5).trim()
    if (!payload || payload === '[DONE]') return
    let event: { type?: string; [key: string]: unknown }
    try {
      event = JSON.parse(payload)
    } catch {
      return // 非 JSON 帧（心跳/注释）忽略
    }
    switch (event.type) {
      case 'step':
        assistant.steps.push({
          tool: String(event.tool ?? ''),
          label: String(event.label ?? '正在处理…'),
        })
        assistant.currentStep = String(event.label ?? '正在处理…')
        break
      case 'token':
        assistant.content += String(event.text ?? '')
        break
      case 'done':
        if (event.conversation_id) {
          conversationId.value = String(event.conversation_id)
        }
        assistant.streaming = false
        assistant.currentStep = null
        break
      case 'error':
        if (event.fallback === true) {
          // 主链路异常已切换 Dify 兜底：清空部分回答与步骤，继续接收兜底输出
          assistant.content = ''
          assistant.steps = []
          assistant.currentStep = null
          assistant.fallback = true
        } else {
          assistant.error = String(event.message ?? 'AI 服务暂时不可用，请稍后再试。')
          assistant.currentStep = null
        }
        break
    }
  }

  /** 发送一条消息（SSE 流式接收）。流式期间忽略新发送。 */
  async function send(text: string): Promise<void> {
    const query = text.trim()
    if (!query || isStreaming.value) return

    turns.value.push(newTurn('user', query))
    const assistant = currentAssistant()
    isStreaming.value = true
    fatalError.value = null
    abortController = new AbortController()

    try {
      const resp = await fetch(API_DIALOGUE_CHAT, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...auth.authHeaders(),
        },
        body: JSON.stringify({
          query,
          streaming: true,
          conversation_id: conversationId.value,
          use_rag: true,
          rag_top_k: 3,
        }),
        signal: abortController.signal,
      })
      if (!resp.ok || !resp.body) {
        assistant.error = `请求失败（HTTP ${resp.status}）`
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // 按 SSE 帧分隔符（空行）切分，最后一段可能不完整留待下轮
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          handleFrame(frame.trim(), assistant)
        }
      }
      // 流结束但未收到 done（异常断开）：尽力展示已收内容
      if (assistant.streaming && !assistant.content && !assistant.error) {
        assistant.error = '回答中断，请重试。'
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        assistant.error = '已取消发送'
      } else {
        assistant.error = e instanceof Error ? e.message : '网络请求失败'
      }
    } finally {
      assistant.streaming = false
      assistant.currentStep = null
      isStreaming.value = false
      abortController = null
    }
  }

  /** 清空对话（会话 id 重置，后端进程内会话随之新建）。 */
  function reset(): void {
    abortController?.abort()
    turns.value = []
    conversationId.value = ''
    isStreaming.value = false
    fatalError.value = null
  }

  return { turns, isStreaming, conversationId, fatalError, send, reset }
}
