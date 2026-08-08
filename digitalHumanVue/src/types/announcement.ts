/** 公告状态：published=已发布（访客可见）/ draft=草稿 */
export type AnnouncementStatus = 'published' | 'draft'

/** 管理端公告条目（含 status 字段） */
export interface AnnouncementItem {
  id: number
  title: string
  content: string
  status: AnnouncementStatus
  pinned: boolean
  created_at: string
  updated_at: string
}

/** 管理端列表响应（分页） */
export interface AnnouncementListResponse {
  items: AnnouncementItem[]
  total: number
  page: number
  page_size: number
}

/** 访客端公告条目（不含 status 字段） */
export interface PublicAnnouncementItem {
  id: number
  title: string
  content: string
  pinned: boolean
  created_at: string
  updated_at: string
}

/** 访客端公告列表响应（仅已发布） */
export interface PublicAnnouncementListResponse {
  items: PublicAnnouncementItem[]
  total: number
}

/** 新建/编辑公告表单数据 */
export interface AnnouncementFormData {
  title: string
  content: string
  status: AnnouncementStatus
  pinned: boolean
}
