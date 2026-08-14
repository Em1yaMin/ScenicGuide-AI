/**
 * 数字人形象 API 封装（对齐后端 /api/v1/dh-character 与
 * /api/admin/dh-character 契约，后端 PR #99）。
 *
 * - 公开 GET：对话页/访客端读取当前形象（失败由调用方回退默认）
 * - 管理端 GET/PUT：管理后台形象管理页；鉴权走 adminApiHeaders 双凭据
 *   （管理员 JWT + X-Admin-Token 向后兼容），非法形象名后端 422
 */
import { API_ADMIN_DH_CHARACTER, API_PUBLIC_DH_CHARACTER } from '@/api'
import type { DhCharacterResponse } from '@/types/dhCharacter'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { adminApiHeaders } from '@/utils/adminHeaders'

/** 读取当前数字人形象（公开接口，对话页动态形象用；失败由调用方回退默认） */
export function fetchPublicCharacter(): Promise<DhCharacterResponse> {
  return fetchWithCodeEnvelope<DhCharacterResponse>(API_PUBLIC_DH_CHARACTER)
}

/** 管理端读取当前形象 + 可选列表（渲染形象卡片网格） */
export function fetchAdminCharacter(): Promise<DhCharacterResponse> {
  return fetchWithCodeEnvelope<DhCharacterResponse>(API_ADMIN_DH_CHARACTER, {
    headers: adminApiHeaders(false),
  })
}

/** 管理端设置当前形象（形象名须在可选列表内，非法 422） */
export function updateAdminCharacter(character: string): Promise<DhCharacterResponse> {
  return fetchWithCodeEnvelope<DhCharacterResponse>(API_ADMIN_DH_CHARACTER, {
    method: 'PUT',
    headers: adminApiHeaders(),
    body: JSON.stringify({ character }),
  })
}
