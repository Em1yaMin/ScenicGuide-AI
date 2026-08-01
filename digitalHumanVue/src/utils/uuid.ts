/**
 * 生成 UUID v4 格式字符串（36 字符）。
 *
 * 优先使用原生 `crypto.randomUUID()`；该 API 仅在安全上下文
 * （HTTPS 或 localhost）可用。通过局域网 IP + 明文 HTTP（如 `http://<ip>:5173`）
 * 访问时不可用，此时降级为 `Math.random` + `Date.now` 组合生成
 * 相同格式的 UUID v4 字符串，保证后端按 visitor_id 字符串处理时兼容。
 */
export function generateUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}
