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
    throw new FetchError(
      `服务器返回错误 (${response.status})`,
      response.status,
    )
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
