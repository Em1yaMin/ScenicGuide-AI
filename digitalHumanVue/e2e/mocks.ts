import type { Page } from '@playwright/test'

/**
 * E2E 后端接口 mock（批次7 C6）。
 *
 * 策略：在浏览器层拦截路径以 /api/ 开头的请求并返回夹具 JSON——前端完全感知不到
 * 后端差异，测试稳定可重复、零外部依赖。所有响应形状对齐后端真实契约
 * （digitalHumanApi/app/schemas/* 与 src/types/*）。
 */

/** 通用夹具数据（与后端 /api/v1/home/highlights 契约一致） */
export const homeHighlights = {
  spots: [
    {
      id: 'spot-1',
      name: '灵山大照壁',
      description: '华夏第一壁，景区入口标志性景观，赵朴初题写鎏金「灵山胜境」',
      tags: ['历史人文'],
      duration_minutes: 30,
      difficulty: 'easy',
      location: '景区入口处',
      highlights: ['赵朴初题字'],
    },
    {
      id: 'spot-2',
      name: '祥符禅寺',
      description: '始建于唐贞观年间的千年古刹，江南千年禅宗祖庭',
      tags: ['历史人文'],
      duration_minutes: 90,
      difficulty: 'easy',
      location: '中轴核心',
      highlights: ['祥符禅钟'],
    },
    {
      id: 'spot-3',
      name: '五印坛城',
      description: '香水海中央的藏式建筑，藏传佛教文化核心区',
      tags: ['历史人文'],
      duration_minutes: 60,
      difficulty: 'easy',
      location: '香水海中央',
      highlights: ['转经筒'],
    },
  ],
  routes: [
    {
      id: 'route-1',
      name: '历史文化深度游',
      description: '大照壁 → 祥符禅寺 → 灵山大佛 → 梵宫 → 五印坛城',
      spot_ids: ['spot-1'],
      tags: ['历史人文'],
      suitable_for: ['文化爱好者'],
      total_duration_minutes: 360,
      difficulty: 'medium',
      recommend_reason: '灵山文化精华一站式体验',
    },
    {
      id: 'route-2',
      name: '自然风光全景游',
      description: '佛足坛 → 菩提大道 → 九龙灌浴 → 灵山大佛 → 曼飞龙塔',
      spot_ids: ['spot-2'],
      tags: ['自然风光'],
      suitable_for: ['摄影爱好者'],
      total_duration_minutes: 300,
      difficulty: 'medium',
      recommend_reason: '俯瞰太湖全景',
    },
    {
      id: 'route-3',
      name: '亲子家庭轻松游',
      description: '九龙灌浴 → 百子戏弥勒 → 梵宫 → 五印坛城',
      spot_ids: ['spot-3'],
      tags: ['亲子'],
      suitable_for: ['家庭出游'],
      total_duration_minutes: 240,
      difficulty: 'easy',
      recommend_reason: '寓教于乐',
    },
  ],
}

/** 访客端公告（仅已发布） */
export const publicAnnouncements = {
  items: [
    {
      id: 1,
      title: '园区五一假期开放时间调整公告',
      content: '五一假期期间园区开放时间延长至 21:00，请游客合理安排行程。',
      pinned: true,
      created_at: '2026-04-20T08:00:00',
      updated_at: '2026-04-20T08:00:00',
    },
  ],
  total: 1,
}

/** 推荐问句（后端本地 LLM 生成，契约 { questions: string[] }） */
export const recommendedQuestions = {
  questions: ['灵山有哪些必打卡景点？', '带老人小孩玩一天怎么安排？', '推荐一条适合拍照的路线'],
}

/**
 * 数字人形象（GET /api/v1/dh-character 与 /api/admin/dh-character，
 * 后端 PR #99 契约 { character, available }；available 13 个与后端常量一致）
 */
export const dhCharacter = {
  character: 'Hiyori',
  available: [
    'Chitose',
    'Epsilon',
    'Haru-1',
    'Haru-2',
    'Hibiki',
    'Hiyori',
    'Izumi',
    'Kei',
    'Mao',
    'Miara',
    'Rice',
    'Shizuku',
    'Tsumiki',
  ],
}

/** 游客 token 签发（POST /api/v1/auth/guest） */
export const guestResponse = {
  access_token: 'e2e-guest-token',
  token_type: 'bearer',
  expires_in: 1800,
  guest_id: 'guest-e2e',
}

/** 登录成功（POST /api/v1/auth/login） */
export const loginResponse = {
  access_token: 'e2e-user-token',
  token_type: 'bearer',
  expires_in: 1440,
  user: {
    id: 1,
    username: 'e2e_admin',
    nickname: 'E2E 测试员',
    created_at: '2026-01-01T00:00:00',
    last_login_at: '2026-08-10T00:00:00',
  },
}

/** 管理员登录成功（POST /api/v1/admin/login，批次9） */
export const adminLoginResponse = {
  access_token: 'e2e-admin-jwt',
  token_type: 'bearer',
  expires_in: 86400,
  username: 'admin',
}

/** 知识库列表（GET /api/admin/knowledge） */
export const knowledgeList = {
  items: [
    {
      id: 1,
      title: '灵山大佛讲解词',
      category: '讲解词',
      content: '灵山大佛通高88米，是世界最高露天青铜释迦牟尼立像……',
      tags: ['灵山大佛', '历史'],
      created_at: '2026-06-01T10:00:00',
      updated_at: '2026-06-01T10:00:00',
    },
    {
      id: 2,
      title: '九龙灌浴演出时间',
      category: 'FAQ',
      content: '九龙灌浴平日演出时间为 10:00、11:30、13:30、15:00……',
      tags: ['九龙灌浴', '演出'],
      created_at: '2026-06-02T10:00:00',
      updated_at: '2026-06-02T10:00:00',
    },
  ],
  total: 2,
  page: 1,
  page_size: 10,
}

/** 景点数据（GET /api/admin/scenic/spots，导览 agent 数据单一来源） */
export const scenicSpots = [
  {
    id: 'spot_001',
    name: '灵山大照壁',
    description: '华夏第一壁，景区入口标志性景观',
    tags: ['历史人文', '拍照打卡'],
    duration_minutes: 30,
    difficulty: '轻松',
    location: '景区入口处，面朝太湖',
    highlights: ['赵朴初题字'],
  },
  {
    id: 'spot_002',
    name: '九龙灌浴',
    description: '花开见佛的音乐动态群雕景观',
    tags: ['历史人文', '演艺'],
    duration_minutes: 60,
    difficulty: '轻松',
    location: '菩提大道北端',
    highlights: ['圣水祈福'],
  },
  {
    id: 'spot_003',
    name: '灵山大佛',
    description: '世界最高露天青铜释迦牟尼立像',
    tags: ['历史人文', '地标'],
    duration_minutes: 120,
    difficulty: '中等',
    location: '秦履峰南麓',
    highlights: ['抱佛脚祈福'],
  },
]

/** 路线数据（GET /api/admin/scenic/routes，导览 agent 数据单一来源） */
export const scenicRoutes = [
  {
    id: 'route_001',
    name: '历史文化深度游',
    description: '灵山文化精华一站式路线',
    spot_ids: ['spot_001', 'spot_003'],
    tags: ['历史人文', '深度游'],
    suitable_for: ['文化爱好者'],
    total_duration_minutes: 360,
    difficulty: '中等',
    recommend_reason: '精华一站式体验',
  },
]

/** 公告列表（GET /api/admin/announcements） */
export const announcementList = {
  items: [
    {
      id: 1,
      title: '园区五一假期开放时间调整公告',
      content: '五一假期期间园区开放时间延长至 21:00。',
      status: 'published',
      pinned: true,
      created_at: '2026-04-20T08:00:00',
      updated_at: '2026-04-20T08:00:00',
    },
    {
      id: 2,
      title: '内部运营周会通知（草稿）',
      content: '每周五下午 15:00 召开运营周会。',
      status: 'draft',
      pinned: false,
      created_at: '2026-07-01T08:00:00',
      updated_at: '2026-07-01T08:00:00',
    },
  ],
  total: 2,
  page: 1,
  page_size: 10,
}

/** 用量总览（GET /api/admin/usage/overview） */
export const usageOverview = {
  start: '2026-08-01T00:00:00',
  end: '2026-08-10T00:00:00',
  granularity: 'day',
  totals: {
    calls: 42,
    input_tokens: 12000,
    output_tokens: 3000,
    total_tokens: 15000,
    avg_duration_ms: 1234,
    cost: null,
  },
  series: [
    {
      bucket: '2026-08-01',
      calls: 10,
      input_tokens: 3000,
      output_tokens: 800,
      total_tokens: 3800,
      avg_duration_ms: 1100,
      cost: null,
    },
    {
      bucket: '2026-08-02',
      calls: 32,
      input_tokens: 9000,
      output_tokens: 2200,
      total_tokens: 11200,
      avg_duration_ms: 1300,
      cost: null,
    },
  ],
  by_model: [
    {
      name: 'deepseek-v4-flash',
      calls: 42,
      input_tokens: 12000,
      output_tokens: 3000,
      total_tokens: 15000,
      avg_duration_ms: 1234,
      cost: null,
    },
  ],
  by_chain: [
    {
      name: 'dialogue',
      calls: 42,
      input_tokens: 12000,
      output_tokens: 3000,
      total_tokens: 15000,
      avg_duration_ms: 1234,
      cost: null,
    },
  ],
}

/** 用量明细（GET /api/admin/usage/details） */
export const usageDetails = {
  total: 2,
  page: 1,
  page_size: 10,
  items: [
    {
      id: 1,
      model: 'deepseek-v4-flash',
      input_tokens: 1000,
      output_tokens: 500,
      total_tokens: 1500,
      duration_ms: 1200,
      chain: 'dialogue',
      user: '1',
      cost: null,
      created_at: '2026-08-10T02:00:00',
    },
    {
      id: 2,
      model: 'deepseek-v4-flash',
      input_tokens: 2000,
      output_tokens: 600,
      total_tokens: 2600,
      duration_ms: 900,
      chain: 'evaluate',
      user: null,
      cost: null,
      created_at: '2026-08-10T01:00:00',
    },
  ],
}

/**
 * 注册全部后端接口 mock。调用方负责在 goto 之前调用。
 * 未命中路由表的 API 请求返回 404 JSON（前端均有降级路径，不崩溃）。
 */
export async function mockApiRoutes(page: Page): Promise<void> {
  // 注意：模式必须带 origin（http://localhost:5199/api/**），不能写成 `**/api/**`——
  // 后者会误伤源码模块 /src/api/*.ts（路径中含 /api/），导致应用模块 404。
  await page.route('http://localhost:5199/api/**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const path = url.pathname
    // 查询参数（供知识库/公告/用量分页等按需透出，冒烟层不校验具体值）
    void url.searchParams

    const respond = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })

    switch (`${method} ${path}`) {
      // ── 访客端 ──
      case 'GET /api/v1/home/highlights':
        return respond(homeHighlights)
      case 'GET /api/v1/announcements':
        return respond(publicAnnouncements)
      case 'POST /api/v1/auth/guest':
        return respond(guestResponse)
      case 'POST /api/v1/auth/login':
        return respond(loginResponse)
      case 'POST /api/v1/admin/login':
        return respond(adminLoginResponse)
      case 'POST /api/v1/visitor/register':
        return respond({ ok: true })
      case 'GET /api/v1/visitor/recommended-questions':
        return respond(recommendedQuestions)
      case 'GET /api/v1/dh-character':
        return respond(dhCharacter)
      // ── 管理端 ──
      case 'GET /api/admin/knowledge':
        return respond(knowledgeList)
      case 'GET /api/admin/dh-character':
        return respond(dhCharacter)
      case 'PUT /api/admin/dh-character': {
        // 契约：返回设置后的形象（后端 set_admin_character 回显新值）
        const body = req.postDataJSON() as { character?: string } | null
        return respond({ ...dhCharacter, character: body?.character ?? dhCharacter.character })
      }
      case 'GET /api/admin/scenic/spots':
        return respond(scenicSpots)
      case 'GET /api/admin/scenic/routes':
        return respond(scenicRoutes)
      case 'GET /api/admin/announcements':
        return respond(announcementList)
      case 'GET /api/admin/usage/overview':
        return respond(usageOverview)
      case 'GET /api/admin/usage/details':
        return respond(usageDetails)
      default:
        return respond({ code: 404, message: `E2E mock 未覆盖: ${method} ${path}` }, 404)
    }
  })
}

/** 在 goto 前调用，收集页面未捕获的 JS 错误，测试末尾断言为空（冒烟级渲染健康检查）。 */
export function attachPageErrorCollector(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (err) => errors.push(err.message))
  return errors
}
