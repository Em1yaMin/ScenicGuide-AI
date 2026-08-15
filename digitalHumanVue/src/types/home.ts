/** 首页热门景点（/api/v1/home/highlights 数据源原样透出） */
export interface HomeSpot {
  id: string
  name: string
  description: string
  /** 实景图 URL（public/images/spots/ 静态资源；缺省时前端回退 emoji 占位） */
  image?: string
  tags: string[]
  duration_minutes: number
  difficulty: string
  location: string
  highlights: string[]
}

/** 首页推荐路线（/api/v1/home/highlights 数据源原样透出） */
export interface HomeRoute {
  id: string
  name: string
  description: string
  spot_ids: string[]
  tags: string[]
  suitable_for: string[]
  total_duration_minutes: number
  difficulty: string
  recommend_reason: string
}

/** 首页活数据响应 */
export interface HomeHighlightsResponse {
  spots: HomeSpot[]
  routes: HomeRoute[]
}
