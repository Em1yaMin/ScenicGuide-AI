<script setup lang="ts">
/**
 * 管理端管理员 Agent 对话页（批次6 B4 前端，演示亮点页）。
 *
 * - SSE 流式对话：token 逐字渲染 + 工具调用步骤气泡（🔍 样式与用户侧
 *   ADH 数字人一致：小号灰底气泡 + 脉冲蓝点）+ done 事件收尾
 * - 二次确认：解析 agent 回答中的 operation_id（删除类工具两阶段契约），
 *   气泡内渲染「确认执行/取消」按钮——确认按钮注入
 *   「确认执行 operation_id: xxx」文本消息自动发送（后端无会话持久化，
 *   新请求内由 agent 调 confirm_operation 真正执行；取消仅本地标记，
 *   待确认项约 120s 自动过期失效）
 * - 会话管理：conversation_id 保存在页面状态跨轮透传（后端仅回显，
 *   连续性由前端携带）
 * - X-Admin-Token 鉴权：token 存 localStorage 的 admin_api_token，
 *   与知识库/公告/记忆管理页一致；未设置/失效时引导打开 Token 弹窗
 */
import { nextTick, onMounted, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { streamAdminAgentChat } from '@/api/adminAgent'
import type { AdminAgentStreamHandlers } from '@/api/adminAgent'

const TOKEN_KEY = 'admin_api_token'

/** 演示引导示例（点击填入输入框，由用户确认后发送） */
const SUGGESTIONS: string[] = [
  '在知识库新增一条讲解词：标题「灵山简介」，内容介绍灵山主峰海拔与云海日出景观',
  '删除知识库中标题为「灵山简介」的条目',
  '查看当前有哪些公告',
  '查看最近 7 天的运营数据概况',
]

/** 工具调用步骤（SSE step 事件透出） */
interface AgentStep {
  tool: string
  label: string
}

/** 待确认的删除操作（两阶段二次确认契约） */
interface PendingConfirm {
  operationId: string
  summary?: string
}

/** 页面消息模型（用户气泡 / agent 气泡共用） */
interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  streaming: boolean // agent 回答流式进行中
  steps: AgentStep[] // agent 工具调用步骤（用户消息恒为空）
  pending: PendingConfirm | null // 解析出的待确认操作（agent 消息）
  cancelled: boolean // 用户已点击「取消」（待确认项本地失效）
  isConfirm: boolean // 用户消息为二次确认注入（「确认执行 operation_id: …」）
  error: string | null // 流式链路错误（error 事件 / fetch 异常）
}

// ── 会话状态 ──
const messages = ref<ChatMessage[]>([])
const input = ref('')
const isStreaming = ref(false)
const conversationId = ref('')
let nextId = 1
const messageListRef = ref<HTMLElement | null>(null)

// ── Token 状态（与知识库/公告页一致的 Token 弹窗模式） ──
const hasToken = ref(Boolean(localStorage.getItem(TOKEN_KEY)))
const showToken = ref(false)
const tokenInput = ref(localStorage.getItem(TOKEN_KEY) ?? '')

// ── 空态欢迎语 ──
const welcomeText =
  '我是管理后台智能助手，可协助您维护系统数据：发布/下架公告、增删改查知识库、' +
  '查询用户记忆与运营数据、重建 RAG 索引等。删除类操作会先展示待确认信息，' +
  '经您确认后才真正执行。'

// ── 消息列表滚动到底部 ──
async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = messageListRef.value
  if (el) el.scrollTop = el.scrollHeight
}

/** 从 agent 回答中提取待确认操作（operation_id 为 32 位小写 hex uuid） */
function extractPending(text: string): PendingConfirm | null {
  if (!text) return null
  // 优先匹配 operation_id 显式标注（容错引号/冒号/中文冒号）
  const explicit = text.match(/operation_id[：:\s]*["'`]?([0-9a-f]{32})/i)
  if (explicit?.[1]) {
    // 摘要：operation_id 之前的文本片段（通常为操作描述）
    const idx = explicit.index ?? 0
    const before = text
      .slice(Math.max(0, idx - 80), idx)
      .replace(/\s+/g, ' ')
      .trim()
    return { operationId: explicit[1], summary: before || undefined }
  }
  // 兜底：任意位置独立出现的 32 位 hex（联调实测 agent 以反引号包裹
  // operation_id，如 「操作 ID：`cb64...`」，边界用非 hex 字符判定更鲁棒）
  const fallback = text.match(/(?:^|[^0-9a-f])([0-9a-f]{32})(?:[^0-9a-f]|$)/i)
  if (fallback?.[1]) {
    const idx = fallback.index ?? 0
    const before = text
      .slice(Math.max(0, idx - 80), idx)
      .replace(/\s+/g, ' ')
      .trim()
    return { operationId: fallback[1], summary: before || undefined }
  }
  return null
}

/** 发送消息（text 缺省取输入框内容；显式传 text（如确认注入）不清空输入框） */
async function send(text?: string): Promise<void> {
  const content = (text ?? input.value).trim()
  if (!content || isStreaming.value) return
  if (text === undefined) input.value = ''
  // 二次确认注入标记：点击「确认执行」自动发送的消息（供回执处理区分）
  const isConfirm = /^确认执行 operation_id:/i.test(content)
  messages.value.push({
    id: nextId++,
    role: 'user',
    content,
    streaming: false,
    steps: [],
    pending: null,
    cancelled: false,
    isConfirm,
    error: null,
  })
  // agent 占位消息：流式事件逐步填充（isConfirm 仅用户消息有意义，恒为 false）
  const assistantMsg: ChatMessage = {
    id: nextId++,
    role: 'assistant',
    content: '',
    streaming: true,
    steps: [],
    pending: null,
    cancelled: false,
    isConfirm: false,
    error: null,
  }
  messages.value.push(assistantMsg)
  isStreaming.value = true
  await scrollToBottom()

  const handlers: AdminAgentStreamHandlers = {
    onStep: (event) => {
      // 连续重复步骤去重（与用户侧 ADH 步骤气泡行为一致）
      const last = assistantMsg.steps[assistantMsg.steps.length - 1]
      if (last && last.tool === event.tool && last.label === event.label) return
      assistantMsg.steps.push({ tool: event.tool, label: event.label })
      void scrollToBottom()
    },
    onToken: (event) => {
      assistantMsg.content += event.text
      void scrollToBottom()
    },
    onDone: (event) => {
      conversationId.value = event.conversation_id
      assistantMsg.streaming = false
      // 回答完成后解析待确认操作（供二次确认按钮渲染）。
      // 上一条用户消息为二次确认注入时，本条回执只是确认执行结果
      // （agent 常回显 operation_id，避免误解析出虚假的确认框）
      const prevUser = [...messages.value].reverse().find((m) => m.role === 'user')
      assistantMsg.pending = prevUser?.isConfirm ? null : extractPending(assistantMsg.content)
      void scrollToBottom()
    },
    onError: (event) => {
      assistantMsg.streaming = false
      assistantMsg.error = event.message
      void scrollToBottom()
    },
  }

  try {
    await streamAdminAgentChat({ query: content, conversation_id: conversationId.value }, handlers)
  } catch (e) {
    // 网络/HTTP 层错误：展示消息并引导设置 Token（401）
    const message = e instanceof Error ? e.message : '请求失败'
    assistantMsg.streaming = false
    assistantMsg.error = message
    if (isUnauthorized(e)) {
      hasToken.value = false
      showToken.value = true
    }
    await scrollToBottom()
  } finally {
    isStreaming.value = false
    void scrollToBottom()
  }
}

/** 判断错误是否为 401（X-Admin-Token 缺失/无效） */
function isUnauthorized(e: unknown): boolean {
  return typeof e === 'object' && e !== null && 'httpStatus' in e && e.httpStatus === 401
}

/** 点击「确认执行」：注入确认消息并自动发送（携带 operation_id 供 agent 调 confirm_operation） */
function confirmOperation(msg: ChatMessage): void {
  if (!msg.pending || isStreaming.value) return
  const { operationId } = msg.pending
  msg.pending = null
  void send(`确认执行 operation_id: ${operationId}`)
}

/** 点击「取消」：本地标记失效（后端待确认项约 120s 自动过期，无需额外请求） */
function cancelOperation(msg: ChatMessage): void {
  msg.pending = null
  msg.cancelled = true
}

/** 建议示例：填入输入框（不自动发送，由用户确认） */
function useSuggestion(text: string): void {
  input.value = text
}

/** Enter 发送 / Shift+Enter 换行（中文输入法组合态不误触发） */
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void send()
  }
}

/** 新会话：清空消息与会话 ID（后端每请求独立，前端携带连续性） */
function resetConversation(): void {
  if (isStreaming.value) return
  messages.value = []
  conversationId.value = ''
}

// ── Token 弹窗（与知识库/公告页一致） ──
function openTokenModal(): void {
  tokenInput.value = localStorage.getItem(TOKEN_KEY) ?? ''
  showToken.value = true
}

function saveToken(): void {
  const trimmed = tokenInput.value.trim()
  if (!trimmed) return
  localStorage.setItem(TOKEN_KEY, trimmed)
  hasToken.value = true
  showToken.value = false
}

/** Markdown 渲染（DOM 净化，与知识库页同模式） */
function renderMarkdown(text: string): string {
  if (!text) return ''
  const raw = marked.parse(text, { async: false }) as string
  return DOMPurify.sanitize(raw)
}

onMounted(() => {
  void scrollToBottom()
})
</script>

<template>
  <div class="admin-agent">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-text">
        <h1>管理员 Agent</h1>
        <p class="header-desc">
          向 AI 助手下达管理指令：发布公告、维护知识库、查询运营数据等（删除类操作需二次确认）
        </p>
      </div>
      <div class="header-actions">
        <button class="secondary-btn" @click="openTokenModal">Token 设置</button>
        <button class="primary-btn" :disabled="isStreaming" @click="resetConversation">
          新会话
        </button>
      </div>
    </div>

    <!-- Token 未设置提示条 -->
    <div v-if="!hasToken" class="token-hint">
      <span>⚠ 未设置管理员 Token（X-Admin-Token），管理员 Agent 不可用。</span>
      <button class="token-hint-btn" @click="openTokenModal">立即设置</button>
    </div>

    <!-- 对话卡片 -->
    <div class="chat-card">
      <div ref="messageListRef" class="message-list">
        <!-- 空态欢迎语 -->
        <div v-if="messages.length === 0" class="empty-state">
          <div class="empty-icon">🤖</div>
          <p class="empty-text">{{ welcomeText }}</p>
        </div>

        <!-- 消息气泡列表 -->
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message-row"
          :class="msg.role === 'user' ? 'row-user' : 'row-agent'"
        >
          <!-- 用户消息：右对齐深色气泡 -->
          <div v-if="msg.role === 'user'" class="bubble bubble-user">
            <span class="bubble-text">{{ msg.content }}</span>
          </div>

          <!-- agent 消息：左对齐浅色气泡 + 步骤气泡 + 二次确认 -->
          <div v-else class="bubble-column">
            <!-- 工具调用步骤气泡（🔍 样式与用户侧 ADH 一致） -->
            <div v-if="msg.steps.length > 0" class="steps">
              <div v-for="(step, i) in msg.steps" :key="`${step.tool}-${i}`" class="step-bubble">
                <span class="step-dot" />
                <span class="step-text">🔍 {{ step.label || step.tool }}</span>
              </div>
            </div>

            <div class="bubble bubble-agent">
              <!-- 回答内容（Markdown，流式逐字增量渲染） -->
              <div v-if="msg.content" class="markdown-body" v-html="renderMarkdown(msg.content)" />
              <!-- 流式等待中且尚无内容 -->
              <span v-else-if="msg.streaming" class="typing-hint">正在思考…</span>
              <!-- 链路错误 -->
              <p v-if="msg.error" class="msg-error">{{ msg.error }}</p>
            </div>

            <!-- 二次确认区：待确认操作摘要 + 确认/取消按钮 -->
            <div v-if="msg.pending" class="confirm-box">
              <div class="confirm-title">⚠️ 删除类操作待确认</div>
              <div v-if="msg.pending.summary" class="confirm-summary">
                {{ msg.pending.summary }}
              </div>
              <div class="confirm-actions">
                <button class="confirm-btn confirm-btn--ok" @click="confirmOperation(msg)">
                  确认执行
                </button>
                <button class="confirm-btn confirm-btn--cancel" @click="cancelOperation(msg)">
                  取消
                </button>
              </div>
            </div>
            <p v-else-if="msg.cancelled" class="confirm-cancelled">
              已取消该操作，待确认项已失效（约 120 秒后后端自动清理）
            </p>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="input-area">
        <!-- 演示引导示例（点击填入输入框） -->
        <div v-if="!isStreaming" class="suggest-bar">
          <span class="suggest-label">试试</span>
          <div class="suggest-scroll">
            <button
              v-for="s in SUGGESTIONS"
              :key="s"
              type="button"
              class="suggest-chip"
              @click="useSuggestion(s)"
            >
              {{ s }}
            </button>
          </div>
        </div>
        <div class="input-row">
          <textarea
            v-model="input"
            rows="2"
            class="input-box"
            placeholder="输入管理指令，Enter 发送（Shift+Enter 换行）"
            :disabled="isStreaming"
            @keydown="onKeydown"
          />
          <button class="send-btn" :disabled="!input.trim() || isStreaming" @click="send()">
            {{ isStreaming ? '生成中…' : '发送' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Token 设置弹窗 -->
    <Teleport to="body">
      <div v-if="showToken" class="modal-overlay" @click.self="showToken = false">
        <div class="modal-panel">
          <div class="modal-header">
            <h2>管理员 Token</h2>
            <button class="icon-btn" @click="showToken = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row">
              <label>X-Admin-Token</label>
              <input
                v-model="tokenInput"
                type="password"
                :placeholder="hasToken ? '已设置' : '请输入 Token'"
              />
              <p class="form-hint">Token 保存在浏览器本地，用于调用管理接口。</p>
            </div>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showToken = false">取消</button>
            <button class="primary-btn" @click="saveToken">保存</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.admin-agent {
  height: calc(100vh - 140px);
  min-height: 520px;
  padding: var(--space-xl) var(--space-2xl);
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
  box-sizing: border-box;
}

/* ── 页面头部 ── */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-md);
  flex-shrink: 0;
}

.header-text h1 {
  font-family: var(--font-display);
  font-size: var(--text-display);
  margin: 0 0 var(--space-xs);
  color: var(--ink);
}

.header-desc {
  font-size: var(--text-body);
  color: var(--slate);
  margin: 0;
}

.header-actions {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.primary-btn,
.secondary-btn {
  padding: var(--space-sm) var(--space-lg);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.primary-btn {
  background: var(--jade);
  color: var(--surface);
}

.primary-btn:hover {
  background: var(--jade-strong);
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.secondary-btn {
  background: transparent;
  color: var(--ink);
  border: 1px solid var(--mist);
}

.secondary-btn:hover {
  background: var(--overlay);
}

/* ── Token 提示条 ── */
.token-hint {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid #e0b94a;
  background: #fdf6e3;
  border-radius: var(--radius-md);
  font-size: var(--text-small);
  color: #7a5b0f;
  flex-shrink: 0;
}

.token-hint-btn {
  margin-left: auto;
  padding: 4px 12px;
  border: 1px solid #d4a92e;
  border-radius: var(--radius-sm);
  background: transparent;
  color: #7a5b0f;
  font-size: var(--text-caption);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}

.token-hint-btn:hover {
  background: rgba(212, 169, 46, 0.15);
}

/* ── 对话卡片 ── */
.chat-card {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-card);
  overflow: hidden;
}

.message-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-lg);
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

/* 空态 */
.empty-state {
  margin: auto;
  max-width: 520px;
  text-align: center;
  color: var(--slate);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-lg);
}

.empty-icon {
  font-size: 2.5rem;
  opacity: 0.5;
}

.empty-text {
  font-size: var(--text-small);
  line-height: var(--line-height-body);
  margin: 0;
}

/* 消息行 */
.message-row {
  display: flex;
  width: 100%;
}

.row-user {
  justify-content: flex-end;
}

.row-agent {
  justify-content: flex-start;
}

/* 用户气泡 */
.bubble-user {
  max-width: 70%;
  padding: var(--space-sm) var(--space-md);
  border-radius: 12px 12px 4px 12px;
  background: var(--ink);
  color: var(--surface);
  font-size: var(--text-body);
  line-height: var(--line-height-body);
  white-space: pre-wrap;
  word-break: break-word;
}

/* agent 消息列（步骤气泡 + 回答 + 确认区纵向排列） */
.bubble-column {
  max-width: 78%;
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  align-items: flex-start;
}

.bubble-agent {
  padding: var(--space-sm) var(--space-md);
  border-radius: 12px 12px 12px 4px;
  background: var(--jade-soft);
  border: 1px solid var(--mist);
  font-size: var(--text-body);
  line-height: var(--line-height-body);
  word-break: break-word;
}

/* 工具调用步骤气泡（🔍 样式与用户侧 ADH 一致） */
.steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

.step-bubble {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 10px 10px 10px 2px;
  background: rgba(30, 42, 38, 0.06);
  font-size: var(--text-caption);
  color: var(--slate);
}

.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--jade);
  animation: pulse 1.2s ease-in-out infinite;
  flex-shrink: 0;
}

.step-text {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

/* Markdown 内容 */
.markdown-body :deep(p) {
  margin: 0 0 0.5em;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0.25em 0;
  padding-left: 1.4em;
}

.markdown-body :deep(li) {
  margin: 0.15em 0;
}

.markdown-body :deep(code) {
  background: rgba(30, 42, 38, 0.06);
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 0.9em;
}

.markdown-body :deep(pre) {
  background: rgba(30, 42, 38, 0.85);
  color: var(--surface);
  padding: var(--space-sm) var(--space-md);
  border-radius: var(--radius-md);
  overflow-x: auto;
  margin: 0.5em 0;
}

.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
}

.markdown-body :deep(strong) {
  font-weight: 600;
}

/* 流式等待 / 错误 */
.typing-hint {
  color: var(--slate);
  font-size: var(--text-small);
}

.msg-error {
  margin: var(--space-xs) 0 0;
  color: var(--cinnabar);
  font-size: var(--text-small);
}

/* 二次确认区 */
.confirm-box {
  border: 1px solid #e0b94a;
  background: #fdf6e3;
  border-radius: var(--radius-md);
  padding: var(--space-sm) var(--space-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
  max-width: 100%;
}

.confirm-title {
  font-size: var(--text-small);
  font-weight: 600;
  color: #7a5b0f;
}

.confirm-summary {
  font-size: var(--text-small);
  color: #7a5b0f;
  word-break: break-all;
}

.confirm-actions {
  display: flex;
  gap: var(--space-sm);
  margin-top: var(--space-xs);
}

.confirm-btn {
  padding: 6px 16px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: var(--text-small);
  font-weight: 500;
  cursor: pointer;
  transition: opacity var(--duration-fast) var(--ease-out);
}

.confirm-btn--ok {
  background: var(--cinnabar);
  color: var(--surface);
}

.confirm-btn--ok:hover {
  background: var(--cinnabar-strong);
}

.confirm-btn--cancel {
  background: transparent;
  color: var(--ink);
  border: 1px solid var(--mist);
}

.confirm-btn--cancel:hover {
  background: var(--overlay);
}

.confirm-cancelled {
  font-size: var(--text-caption);
  color: var(--slate);
  margin: 0;
}

/* ── 输入区 ── */
.input-area {
  border-top: 1px solid var(--mist);
  padding: var(--space-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  background: var(--surface);
  flex-shrink: 0;
}

.suggest-bar {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.suggest-label {
  flex-shrink: 0;
  font-size: var(--text-caption);
  color: var(--slate);
}

.suggest-scroll {
  display: flex;
  gap: var(--space-sm);
  overflow-x: auto;
  scrollbar-width: none;
}

.suggest-scroll::-webkit-scrollbar {
  display: none;
}

.suggest-chip {
  flex-shrink: 0;
  padding: 5px 12px;
  border: 1px solid var(--mist);
  border-radius: var(--radius-full);
  background: var(--surface);
  font-size: var(--text-caption);
  color: var(--ink);
  cursor: pointer;
  user-select: none;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.suggest-chip:hover {
  border-color: var(--jade);
  background: var(--jade-soft);
}

.input-row {
  display: flex;
  align-items: flex-end;
  gap: var(--space-sm);
}

.input-box {
  flex: 1;
  resize: none;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  font-size: var(--text-body);
  font-family: var(--font-body);
  line-height: var(--line-height-body);
  background: var(--surface);
  color: var(--ink);
  transition: border-color var(--duration-fast) var(--ease-out);
}

.input-box:focus {
  outline: none;
  border-color: var(--jade);
}

.input-box:disabled {
  opacity: 0.6;
}

.send-btn {
  padding: var(--space-sm) var(--space-xl);
  border: none;
  border-radius: var(--radius-sm);
  background: var(--jade);
  color: var(--surface);
  font-size: var(--text-small);
  font-weight: 500;
  cursor: pointer;
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
  white-space: nowrap;
}

.send-btn:hover {
  background: var(--jade-strong);
}

.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ── Token 弹窗（与知识库/公告页一致） ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(30, 42, 38, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal-panel {
  width: 420px;
  max-width: calc(100vw - var(--space-xl));
  background: var(--surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-pop);
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-lg) var(--space-xl);
  border-bottom: 1px solid var(--mist);
}

.modal-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
}

.icon-btn {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--slate);
  font-size: var(--text-small);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}

.icon-btn:hover {
  background: var(--overlay);
}

.modal-body {
  padding: var(--space-lg) var(--space-xl);
}

.form-row label {
  display: block;
  font-size: var(--text-small);
  color: var(--slate);
  margin-bottom: var(--space-xs);
}

.form-row input {
  width: 100%;
  box-sizing: border-box;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-body);
  background: var(--surface);
  color: var(--ink);
}

.form-row input:focus {
  outline: none;
  border-color: var(--jade);
}

.form-hint {
  margin: var(--space-xs) 0 0;
  font-size: var(--text-caption);
  color: var(--slate);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-sm);
  padding: var(--space-md) var(--space-xl);
  border-top: 1px solid var(--mist);
}

/* ── 响应式 ── */
@media (max-width: 900px) {
  .admin-agent {
    height: auto;
    min-height: calc(100vh - 160px);
    padding: var(--space-lg);
  }

  .chat-card {
    min-height: 480px;
  }

  .bubble-user,
  .bubble-column {
    max-width: 88%;
  }
}
</style>
