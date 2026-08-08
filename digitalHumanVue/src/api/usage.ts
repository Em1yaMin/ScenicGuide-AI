/**
 * 管理端 LLM 用量报表 API 封装（对齐后端 PR #54 /api/admin/usage 契约，
 * 权威文档见 digitalHumanApi/doc/usage-tracking.md）。
 * 全部接口需 X-Admin-Token 鉴权（token 存 localStorage 的 admin_api_token，
 * 与知识库/记忆管理页一致）。
 */
import { API_ADMIN_USAGE_DETAILS, API_ADMIN_USAGE_EXPORT, API_ADMIN_USAGE_OVERVIEW } from '@/api'
import type {
  UsageDetailResponse,
  UsageDetailsParams,
  UsageOverviewParams,
  UsageOverviewResponse,
} from '@/types/usage'
import { FetchError, fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

const TOKEN_KEY = 'admin_api_token'

/** 构造带 X-Admin-Token 的请求头（json=true 时附带 Content-Type） */
function adminHeaders(json = true): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Admin-Token': localStorage.getItem(TOKEN_KEY) ?? '',
  }
  if (json) headers['Content-Type'] = 'application/json'
  return headers
}

/** 查询参数 → querystring（空值忽略；page/page_size 为数字） */
function buildQuery(params: UsageDetailsParams): string {
  const qs = new URLSearchParams()
  if (params.start) qs.append('start', params.start)
  if (params.end) qs.append('end', params.end)
  if (params.granularity) qs.append('granularity', params.granularity)
  if (params.model) qs.append('model', params.model)
  if (params.chain) qs.append('chain', params.chain)
  if (params.user) qs.append('user', params.user)
  if (params.page !== undefined) qs.append('page', String(params.page))
  if (params.page_size !== undefined) qs.append('page_size', String(params.page_size))
  return qs.toString()
}

/** 用量总览（趋势序列 + 按模型/链路聚合 + 合计） */
export function fetchUsageOverview(
  params: UsageOverviewParams = {},
): Promise<UsageOverviewResponse> {
  return fetchWithCodeEnvelope<UsageOverviewResponse>(
    `${API_ADMIN_USAGE_OVERVIEW}?${buildQuery(params)}`,
    { headers: adminHeaders(false) },
  )
}

/** 用量明细（分页，created_at 倒序；可按模型/链路/用户筛选） */
export function fetchUsageDetails(params: UsageDetailsParams = {}): Promise<UsageDetailResponse> {
  return fetchWithCodeEnvelope<UsageDetailResponse>(
    `${API_ADMIN_USAGE_DETAILS}?${buildQuery(params)}`,
    { headers: adminHeaders(false) },
  )
}

/**
 * CSV 导出（与明细同筛选、全量行；UTF-8 BOM 兼容 Excel）。
 * 返回 Blob，由调用方触发下载（fetchWithCodeEnvelope 面向 JSON，
 * 这里用原生 fetch 处理二进制响应）。
 */
export async function exportUsageCsv(params: UsageDetailsParams = {}): Promise<Blob> {
  const response = await fetch(`${API_ADMIN_USAGE_EXPORT}?${buildQuery(params)}`, {
    headers: adminHeaders(false),
  })
  if (!response.ok) {
    let message = `导出失败（HTTP ${response.status}）`
    try {
      const body = (await response.json()) as { detail?: unknown }
      if (body?.detail) message = String(body.detail)
    } catch {
      // 非 JSON 响应（如代理层错误页），保留默认信息
    }
    throw new FetchError(message, response.status)
  }
  return response.blob()
}
