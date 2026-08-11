/**
 * 带双向错误检查的 fetch 封装。
 *
 * 1. response.ok — 覆盖 nginx/Vite 代理等中间层非 JSON 错误
 * 2. result.code — 覆盖后端业务层 ErrorResponse
 *
 * 成功时返回解析后的 JSON body（不剥离 code 字段，保持向后兼容）。
 */

/** 默认请求超时时间（毫秒）。设为 0 禁用超时。 */
const DEFAULT_TIMEOUT_MS = 15_000

/**
 * 分析类接口（AI 运营分析 / usage）专用超时（毫秒）。
 *
 * /api/v1/analysis/* 需调 LLM（opencode-go/官方 API 延迟波动大），后端预算
 * LLM_REQUEST_TIMEOUT=180s / LLM_ANALYSIS_TIMEOUT=240s；默认 15s 会被慢请求
 * 掐断报「请求超时 (15000ms)」，故调用点必须显式传入本常量覆盖默认值。
 */
export const ANALYSIS_TIMEOUT_MS = 300_000

/** fetchWithCodeEnvelope 的扩展选项 */
export interface FetchOptions extends RequestInit {
  /** 请求超时时间（毫秒），默认 15_000。设为 0 禁用超时。 */
  timeoutMs?: number
}

export interface CodeEnvelope {
  code?: number
  message?: string
  [key: string]: unknown
}

export class FetchError extends Error {
  constructor(
    message: string,
    public readonly httpStatus?: number,
    public readonly appCode?: number,
    public readonly cause?: unknown,
  ) {
    super(message, cause ? { cause } : undefined)
    this.name = 'FetchError'
  }
}

export async function fetchWithCodeEnvelope<T = unknown>(
  url: string,
  options?: FetchOptions,
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchInit } = options ?? {}

  let controller: AbortController | undefined
  let timeoutId: ReturnType<typeof setTimeout> | undefined

  if (timeoutMs > 0 && !fetchInit.signal) {
    controller = new AbortController()
    fetchInit.signal = controller.signal
    timeoutId = setTimeout(() => controller!.abort(), timeoutMs)
  }

  let response: Response
  try {
    response = await fetch(url, fetchInit)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      if (controller) {
        throw new FetchError(`请求超时 (${timeoutMs}ms)`)
      }
      throw e
    }
    throw new FetchError(
      e instanceof Error ? `网络请求失败: ${e.message}` : '网络请求失败',
      undefined,
      undefined,
      e,
    )
  } finally {
    if (timeoutId) clearTimeout(timeoutId)
  }

  if (!response.ok) {
    // FastAPI HTTPException 返回 { detail }；解析出消息以提供友好错误提示
    // （如 401 "用户名或密码错误"、409 "用户名已存在"、422 字段校验）。
    const detail = await extractErrorDetail(response)
    throw new FetchError(detail || `服务器返回错误 (${response.status})`, response.status)
  }

  let body: CodeEnvelope
  try {
    body = await response.json()
  } catch (e) {
    throw new FetchError('响应不是有效的 JSON 格式', undefined, undefined, e)
  }

  if (body.code && body.code !== 200) {
    throw new FetchError(
      body.message || `业务错误 (code: ${body.code})`,
      response.status,
      body.code,
    )
  }

  return body as T
}

/**
 * 从非 OK 响应中提取后端错误消息（FastAPI 的 { detail } 字段）。
 *
 * 兼容三种形态：
 * - 字符串：HTTPException(detail="...") → 直接使用
 * - 数组：422 校验错误 [{ loc, msg }] → 拼接为可读消息
 * - 无法解析 / 无 detail：返回空串，由调用方回退通用消息
 */
async function extractErrorDetail(response: Response): Promise<string> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    return ''
  }
  if (typeof body !== 'object' || body === null) return ''

  const detail = (body as CodeEnvelope).detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item !== 'object' || item === null) return null
        const { msg, loc } = item as { msg?: unknown; loc?: unknown }
        if (typeof msg !== 'string' || !msg.trim()) return null
        if (Array.isArray(loc)) {
          const field = loc
            .filter((part): part is string => typeof part === 'string' && part !== 'body')
            .join('.')
          return field ? `${field}: ${msg}` : msg
        }
        return msg
      })
      .filter((m): m is string => m !== null)
    return messages.join('；')
  }
  return ''
}
