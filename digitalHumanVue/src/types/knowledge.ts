export interface KnowledgeItem {
  id: number
  title: string
  category: string
  content: string
  tags: string[] | null
  created_at: string
  updated_at: string
}

export interface KnowledgeListResponse {
  items: KnowledgeItem[]
  total: number
  page: number
  page_size: number
}

export interface KnowledgeFormData {
  title: string
  category: string
  content: string
  tags: string
}

export interface KnowledgeQuery {
  category?: string
  tag?: string
  keyword?: string
  page?: number
  page_size?: number
}
