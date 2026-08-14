/**
 * 数字人形象类型（对齐后端 /api/v1/dh-character 与 /api/admin/dh-character
 * 契约，后端常量见 digitalHumanApi/app/models/app_setting.py）。
 *
 * 形象列表以后端 available 字段为准（13 个，与 ADH public/characters/
 * 目录一致），前端不硬编码第二份列表；预览图 URL 按名称拼接
 * `{VITE_LIGHT4AI_URL}/characters/{name}/{name}.png`。
 */

/** 数字人形象响应（访客端/管理端共用）：当前形象 + 可选列表 */
export interface DhCharacterResponse {
  /** 当前生效形象名（如 "Hiyori"） */
  character: string
  /** 可选形象列表（13 个，与后端常量一致） */
  available: string[]
}
