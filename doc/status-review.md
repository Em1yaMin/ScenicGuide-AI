# 项目状态审视 — 设计 vs 实现偏离分析（v2 回退后）

> 审视日期: 2026-07-12
> 对照基准: doc/architecture-analysis.md + doc/decision-questions.md
> 审视范围: digitalHumanApi/ + digitalHumanVue/src/
> 版本: v2 — 执行 ADR-004 回退超前实现功能后

## 一、结论先行

**架构已严格对齐设计文档原貌。** 通过 ADR-004 决策，删除了三项超前实现功能（个性化推荐、景区数据本地同步、景点维度分析）及其连锁代码。当前架构精简到设计文档定义的核心链路：数字人对话 → 轮询抓取 → 量化分析 → 数据大屏 / 感受度报告。

剩余待办从 12 项缩减到 7 项，且全部聚焦核心链路打通。

## 二、回退操作记录

### 已删除的后端代码

| 文件 | 操作 | 原因 |
|------|------|------|
| `app/models/scenic_spot.py` | 删除 | 景区数据本地表属于超前实现 |
| `app/services/scenic_sync.py` | 删除 | Dify 知识库同步逻辑超前实现 |
| `app/tasks/scenic_sync.py` | 删除 | 5 分钟轮询静态数据，浪费 API |
| `app/schemas/common.py` | 删除 | SpotSentimentDist 无人引用 |
| `tests/test_scenic_sync.py` | 删除 | 跟随 service 删除 |
| `app/main.py` | 修改 | 移除 scenic_sync_loop 启动 |
| `app/services/dify_client.py` | 修改 | 移除 get_datasets / get_dataset_documents |
| `app/services/visitor_service.py` | 重写 | 只保留 register_visitor |
| `app/routers/visitor.py` | 重写 | 只保留 /register 端点 |
| `app/schemas/visitor.py` | 重写 | 只保留注册相关 schema |
| `app/routers/admin.py` | 重写 | 移除 /spot-analysis 端点 |
| `app/services/admin_service.py` | 修改 | 移除 get_spot_analysis 函数 |
| `app/schemas/admin.py` | 重写 | 移除 SpotAnalysis 相关 schema |
| `app/models/__init__.py` | 修改 | 移除 ScenicSpot 导入 |
| `tests/test_visitor.py` | 重写 | 移除 recommend/spot-detail 测试 |
| `tests/test_admin.py` | 重写 | 移除 spot-analysis 测试 |

### 已删除的前端代码

| 文件 | 操作 | 原因 |
|------|------|------|
| `src/views/RecommendView.vue` | 删除 | 个性化推荐超前实现 |
| `src/views/SpotDetailView.vue` | 删除 | 依赖已删除的 scenic_spots 表 |
| `src/views/SpotAnalysisView.vue` | 删除 | 景点维度分析超前实现 |
| `src/composables/useFormatAgeRec.ts` | 删除 | 仅被 SpotDetailView 使用 |
| `src/router/index.ts` | 修改 | 移除 recommend / spot / spot-analysis 路由 |
| `src/layouts/VisitorLayout.vue` | 修改 | 移除「推荐」导航 |
| `src/layouts/AdminLayout.vue` | 修改 | 移除「景点分析」导航 |
| `src/views/HomeView.vue` | 修改 | 副标题去掉「个性化推荐」 |

## 三、当前架构总览（回退后）

### 模块清单

**后端 digitalHumanApi**
```
app/
├── main.py              — FastAPI 入口，启动 polling + analysis 两个后台任务
├── core/
│   ├── config.py        — 配置（Dify API、DB、CORS）
│   └── database.py      — SQLAlchemy 异步引擎
├── models/
│   ├── visitor.py       — 游客表
│   ├── dialogue.py      — 对话记录表
│   ├── evaluation.py    — 量化评估结果表
│   └── stat.py          — 聚合统计表（预留）
├── routers/
│   ├── visitor.py       — POST /api/v1/visitor/register
│   ├── admin.py         — GET /api/v1/admin/dashboard | /report
│   ├── dialogue.py      — GET /api/v1/dialogue/poll （placeholder）
│   └── analysis.py      — POST /api/v1/analysis/batch （placeholder）
├── services/
│   ├── dify_client.py   — Dify API 客户端（conversations / messages / workflows/run）
│   ├── visitor_service.py — register_visitor
│   └── admin_service.py — get_dashboard_stats / get_report_data
├── tasks/
│   ├── polling.py       — 30秒轮询 Dify 拉对话
│   └── analysis.py      — 5分钟批量调量化工作流
└── schemas/             — Pydantic 响应模型
```

**前端 digitalHumanVue/src**
```
src/
├── views/
│   ├── HomeView.vue         — 游客首页
│   ├── ChatView.vue         — iframe 嵌入 light4ai 数字人
│   ├── DashboardView.vue    — 管理后台数据大屏
│   └── ReportView.vue       — 管理后台感受度报告
├── layouts/
│   ├── VisitorLayout.vue    — 游客端布局（首页 + 数字人）
│   └── AdminLayout.vue      — 管理后台布局（数据大屏 + 感受度报告）
├── stores/visitor.ts        — 游客 ID（localStorage UUID）
├── composables/useECharts.ts — ECharts 封装
├── types/dashboard.ts       — 大屏类型定义
└── router/index.ts          — 路由
```

### 数据流链路

```
游客端 Vue3
  ├── 首页 (HomeView)
  └── 数字人 (ChatView) ──iframe──→ light4ai ──→ Dify Chat Agent + 知识库
                                                  │ (旁路)
                                                  ▼
  FastAPI 后台任务
  ├── polling_loop (30s) ──→ Dify /conversations → /messages → 去重入库
  └── analysis_loop (5min) ──→ 找未评估对话 → Dify /workflows/run → 评分入库

管理后台 Vue3
  ├── 数据大屏 (DashboardView) ──→ GET /admin/dashboard
  └── 感受度报告 (ReportView) ──→ GET /admin/report
```

### 数据库表（4张）

| 表 | 用途 | 来源 |
|----|------|------|
| visitors | 游客身份 | POST /visitor/register |
| dialogues | 对话记录 | 对话链路直接落库（`record_dialogue`，deepagents 本地对话）+ polling_loop（Dify 历史） |
| evaluations | 量化评分 | analysis_loop 产出 |
| stats | 聚合统计（预留，当前未使用） | — |

## 四、剩余待办（精简后）

回退后，原 12 项待办缩减为 7 项，全部聚焦核心链路：

| 编号 | 问题 | 严重度 | 位置 |
|------|------|--------|------|
| **A** | iframe URL 带 visitor_id，light4ai 是否透传到 Dify conversation metadata 未验证 | **阻塞** | ChatView + polling.py |
| **B** | sentiment_trend 硬编码 "today" 单点 | 高 | admin_service.py |
| **C** | Dialogue.timestamp 用本地 now 而非 Dify created_at | 高 | tasks/polling.py |
| **G** | dialogue/analysis 两个 router 是 placeholder | 低 | routers/ |
| **H** | Dify Console API 与 App API 鉴权可能混淆 | 中 | config.py + dify_client.py |
| **I** | 补充文档：架构现状 vs 设计 | 低 | doc/ |
| **新增** | 删除 .db 文件中残留的 scenic_spots 表（如有） | 低 | digital_human.db |

原 D/E/F（scenic_sync 相关）已随删除消除。原 J/K/L（加分项）不再追求。

## 五、整体评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 设计对齐度 | 10/10 | ADR-004 后严格对齐设计文档 |
| 实现完整度 | 6/10 | 核心链路骨架齐全，但 A/B/C 三处需修复 |
| 风险可控性 | 6/10 | 风险 A 仍是阻塞项，需优先验证 |
| 演示就绪度 | 5/10 | 修复 A/B/C 后可达 8/10 |

**核心建议**：当前架构精简健康，接下来全部精力投入 A/B/C 三项核心修复，目标是让「对话→抓取→量化→大屏」端到端跑通真实数据。
