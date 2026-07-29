# 项目推进计划书

> 编制日期：2026-07-13
> 项目：景区导览 AI 数字人（第十五届中国软件杯 A5）
> 决策基准：ADR-001~004 + 方案 A（自部署数字人替代 light4ai）

## 一、项目现状

### 已完成

| 模块 | 状态 | 说明 |
|------|------|------|
| 架构设计 | ✅ | 7 项核心决策落地，ADR-001~004 记录 |
| 后端骨架 | ✅ | FastAPI 4 路由 + 2 后台任务 + 4 张表 |
| 前端骨架 | ✅ | Vue3 游客端(首页+对话) + 管理后台(大屏+报告) |
| Dify API 配置 | ✅ | 双 Key（Chat Agent + Workflow），地址已填 |
| Dify API 代码修复 | ✅ | /messages 路径、user 参数、timestamp 均已修复 |
| 超前实现回退 | ✅ | ADR-004 删除推荐/景区同步/景点分析 |
| 前端 .env | ✅ | VITE_LIGHT4AI_URL 已配（待改为本地数字人） |

### 未完成

| 编号 | 内容 | 严重度 |
|------|------|--------|
| **方案A** | 部署live2d-frontend 替代 light4ai | 阻塞 |
| **B** | sentiment_trend 硬编码 "today" 单点 | 高 |
| **G** | dialogue/analysis 占位 API | 低 |
| **I** | 补充架构现状文档 | 低 |
| **DB** | 清理 digital_human.db 残留 scenic_spots 表 | 低 |
| **验证** | polling + analysis 端到端跑通真实数据 | 阻塞 |

## 二、架构决策更新

### ADR-005: 自部署数字人替代 light4ai（方案 A）

**Status**: Accepted

**Context**: light4ai 端配置 API 遇到阻碍，且 light4ai 传给 Dify 的 user 是固定值（adh/YOUR_DIFY_USER），不可控。获取了 awesome-digital-human-live2d-2.0.0 源码，该项目的 difyAgent.yaml 默认 user 正是 "adh"——light4ai 就是用了这个项目的配置。

**Decision**: 采用方案 A——live2d-frontend 独立部署，Vue3 通过 iframe 嵌入。live2d-frontend 后端跑 8001 端口（避开 FastAPI 的 8000），前端跑 3000 端口。difyAgent 的 user 配置为 YOUR_DIFY_USER，与 polling 的 DIFY_LIGHT4AI_USER 对齐。

**Consequences**:
- ✅ 数字人服务完全自控，user 字段可配置
- ✅ 支持All-in-Dify 模式（ASR/TTS/Agent 全走 Dify）
- ✅ FastAPI 旁路抓取逻辑不变
- ✅ 改动最小（ChatView 只改 iframe URL）
- ⚠️ 两个前端服务（Vue3 5173 + Next.js 3000）
- ⚠️ iframe 跨域仍在（live2d-frontend CORS 已设 *）
- ⚠️ 需要 ffmpeg 依赖

### 当前架构总览

```
┌─ 本地 ──────────────────────────────────────────────────────┐
│                                                              │
│  Vue3 游客端 (5173)          Vue3 管理后台 (5173)            │
│  ├── 首页                    ├── 数据大屏                    │
│  └── 数字人对话 ──iframe──→  Next.js 数字人前端 (3000)       │
│                                │                              │
│                                ▼                              │
│                  live2d-frontend 后端 FastAPI (8001)                  │
│                  ├── ASR (Dify)                               │
│                  ├── TTS (Dify)                               │
│                  └── Agent (Dify Chat Agent)                  │
│                                                              │
│  业务后端 FastAPI (8000)                                     │
│  ├── polling_loop (30s) → Dify /conversations + /messages   │
│  ├── analysis_loop (5min) → Dify /workflows/run              │
│  └── SQLite (visitors, dialogues, evaluations, stats)       │
│                                                              │
└──────────────────────┬───────────────────────────────────────┘
                       │ 远程 API
                       ▼
┌─ 服务器 YOUR_DIFY_SERVER_IP ──────────────────────────────────────┐
│  Dify (Docker)                                               │
│  ├── Chat Agent + 景区知识库 (user=YOUR_DIFY_USER)                │
│  └── 量化评估 Workflow                                       │
└──────────────────────────────────────────────────────────────┘
```

## 三、分阶段推进计划

### Phase 1：数字人服务部署（方案 A）

**目标**：live2d-frontend 跑起来，数字人能对话
**预计**：半天

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| 1.1 | 复制live2d-frontend到 `E:\project_file\digitalHuman\digitalHumanLive2D\` | 目录结构完整 |
| 1.2 | 安装后端依赖：`pip install -r requirements.txt` | 无报错 |
| 1.3 | 安装 ffmpeg（Windows 下载放入 PATH） | `ffmpeg -version` 能执行 |
| 1.4 | 配置 `configs/config.yaml`：用 `config_template.yaml` 复制为 `config.yaml`，端口改为 8001 | 保留多引擎模式 |
| 1.5 | config.yaml 中：TTS DEFAULT → `difyAPI.yaml`，Agent DEFAULT → `difyAgent.yaml` | TTS/Agent 走 Dify |
| 1.6 | 配置 `configs/agents/difyAgent.yaml`：DIFY_API_USER 改为 YOUR_DIFY_USER | yaml 更新 |
| 1.7 | **新增** 创建 `configs/engines/asr/tencentASR.yaml`，声明 `SECRET_ID` / `SECRET_KEY` / `APP_ID` 参数 | 配置文件就位 |
| 1.8 | **新增** 实现 `digitalHuman/engine/asr/tencentASR.py`：继承 BaseEngine，`@ASREngines.register("TencentASR")`，调腾讯云实时语音识别 WebSocket API | 引擎可加载 |
| 1.9 | **新增** config.yaml 中 ASR 的 SUPPORT_LIST 加 `tencentASR.yaml`，DEFAULT 改为 `tencentASR.yaml` | 引擎切换生效 |
| 1.10 | 启动后端：`python main.py` | 日志显示 EnginePool 加载 TencentASR，监听 8001 |
| 1.11 | 安装前端依赖：`cd web && pnpm install` | node_modules 就位 |
| 1.12 | 启动前端：`pnpm run dev`（端口 3000） | 浏览器能打开 localhost:3000 |
| 1.13 | 前端 Settings → Agent：填入 Dify API URL + Chat Agent Key | 配置保存 |
| 1.14 | 前端 Settings → TTS：填入 Dify TTS API Key（`app-YOUR_TTS_WF_KEY`） | 配置保存 |
| 1.15 | 前端 Settings → ASR：填入腾讯云 SecretId / SecretKey / AppId | 配置保存 |
| 1.16 | 测试对话：文字输入 → 数字人回答 | 能收到 Dify 回答 |
| 1.17 | 测试语音：按 Ctrl+M 说话 → 腾讯云 ASR → Dify Agent → Dify TTS 播放 | 语音链路通 |
| 1.18 | 改 Vue3 `.env`：`VITE_LIGHT4AI_URL=http://localhost:3000` | iframe 能加载数字人 |

**前置条件**：
- Dify Chat Agent 需开启「文字转语音」功能（TTS 走 Dify）
- 腾讯云账号开通「语音识别」服务，获取 SecretId / SecretKey / AppId
- Dify Chat Agent **无需**开启「语音转文字」（ASR 已改为腾讯云）

**架构说明**：ASR 改用腾讯云实时语音识别（`wss://asr.cloud.tencent.com`），TTS 和 Agent 仍走 Dify。因此基础配置用 `config_template.yaml`（多引擎混合模式），而非 `config_all_in_dify.yaml`。

**风险**：
- ffmpeg 在 Windows 上的安装可能遇到 PATH 问题 → 用 `winget install ffmpeg` 或手动下载
- 腾讯云 ASR WebSocket 签名鉴权实现较复杂 → 可参考 `tencentcloud-sdk-python` 或手写 HMAC-SHA1
- 音频格式不匹配 → 检查live2d-frontend麦克风录制格式（通常 16kHz mono PCM）
- pnpm 未安装 → `npm install -g pnpm`

### Phase 2：核心数据链路验证

**目标**：polling 能拉到真实对话，analysis 能触发量化评分
**预计**：半天

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| 2.1 | 启动业务后端 FastAPI（8000 端口） | uvicorn 启动无报错 |
| 2.2 | 启动 Vue3 前端（5173 端口） | 页面能打开 |
| 2.3 | 在数字人页面和数字人对话 2-3 句 | 对话正常 |
| 2.4 | 等 30 秒，看 FastAPI 日志 | 出现 `Polled N new messages` |
| 2.5 | 查 SQLite 数据库 dialogues 表 | 有真实对话记录 |
| 2.6 | 等 5 分钟，看 analysis_loop 日志 | 出现 `Evaluated N dialogues` |
| 2.7 | 查 evaluations 表 | 有量化评分记录 |
| 2.8 | 打开管理后台数据大屏 | 有数据展示 |
| 2.9 | 打开感受度报告 | 有数据展示 |

**风险**：
- polling 拉不到数据 → 检查 DIFY_LIGHT4AI_USER 是否和live2d-frontend 配置的 user 一致
- analysis 报错 → 检查 DIFY_WORKFLOW_API_KEY 是否正确，Workflow 应用是否已发布
- evaluations 表有记录但字段为空 → 检查 Dify Workflow 输出的 JSON 格式是否和代码解析匹配

### Phase 3：数据展示修复

**目标**：修复已知的数据展示问题
**预计**：2 小时

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| 3.1 | 修复 `sentiment_trend` 按日分组（风险 B） | 大屏趋势图显示多日数据 |
| 3.2 | 处理 dialogue/analysis 占位 API（风险 G） | 要么删除，要么改成手动触发 |
| 3.3 | 清理 digital_human.db 残留 scenic_spots 表 | SQLite 只剩 4 张业务表 |

**3.1 详细方案**：
- `admin_service.py::get_dashboard_stats` 中，将 `sentiment_trend` 改为按 `DATE(dialogues.timestamp)` 分组
- 回溯最近 7 天，每天一个数据点
- 前端 `DashboardView.vue` 的 `xAxis` 改为日期数组

**3.2 详细方案**：
- `dialogue/poll` 改为 `POST /api/v1/dialogue/sync-now`，手动触发一次 poll_once()
- `analysis/batch` 改为 `POST /api/v1/analysis/run-now`，手动触发一次 batch_evaluate()
- 演示时可手动触发立即出数据，加分

### Phase 4：文档收尾

**目标**：文档完备
**预计**：2 小时

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| 4.1 | 更新 `doc/architecture-analysis.md` 追加 ADR-005 | 文档反映当前架构 |
| 4.2 | 更新 `doc/status-review.md` 标记已完成项 | 状态准确 |
| 4.3 | 编写部署文档：本地启动步骤（三个服务） | 评委能照着跑 |
| 4.4 | 编写 `.env` 配置说明 | 每个 Key 的获取方式 |

## 四、服务启动顺序（日常开发）

```bash
# 1. 启动 Dify（已在服务器 YOUR_DIFY_SERVER_IP 上跑着，无需操作）

# 2. 启动数字人后端（live2d-frontend）
cd E:\project_file\digitalHuman\digitalHumanLive2D
python main.py
# 监听 8001

# 3. 启动数字人前端（live2d-frontend）
cd E:\project_file\digitalHuman\digitalHumanLive2D\web
pnpm run dev
# 监听 3000

# 4. 启动业务后端（FastAPI）
cd E:\project_file\digitalHuman\digitalHumanApi
uvicorn app.main:app --reload --port 8000
# 监听 8000

# 5. 启动业务前端（Vue3）
cd E:\project_file\digitalHuman\digitalHumanVue
npm run dev
# 监听 5173
```

访问入口：`http://localhost:5173`（游客端 / 管理后台）

## 五、端口分配

| 服务 | 端口 | 说明 |
|------|------|------|
| Dify | 80 (远程) | YOUR_DIFY_SERVER_IP |
| 数字人后端 | 8001 | live2d-frontend FastAPI |
| 数字人前端 | 3000 | live2d-frontend Next.js |
| 业务后端 | 8000 | FastAPI（polling + analysis + API） |
| 业务前端 | 5173 | Vue3（游客端 + 管理后台） |

## 六、风险矩阵

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| Dify Chat Agent 未开启 TTS | 中 | 高 | TTS 走 Dify，Phase 1 前检查 Dify 后台 Chat Agent 的 TTS 功能 |
| 腾讯云 ASR 签名鉴权实现超预期 | 中 | 高 | 优先用 `tencentcloud-sdk-python` 的 ASR SDK；备选手写 HMAC-SHA1 签名 |
| ffmpeg 安装失败 | 低 | 中 | 用 winget 或手动下载 zip |
| polling 拉不到对话 | 中 | 高 | 检查 user 是否一致、Dify 是否有对话记录 |
| Workflow 输出格式不匹配 | 中 | 中 | 检查 Workflow 输出 JSON，调整 analysis.py 解析 |
| live2d-frontend 前端 CORS 问题 | 低 | 中 | live2d-frontend已设 allow_origins=["*"] |
| 演示时网络不稳定 | 低 | 高 | 提前在本地缓存对话数据，大屏有兜底数据 |

## 七、交付物清单

### 代码交付
- `digitalHumanApi/` — FastAPI 业务后端
- `digitalHumanVue/` — Vue3 前端
- `digitalHumanLive2D/` — 数字人服务（live2d-frontend，配置文件已改）

### 文档交付
- `doc/architecture-analysis.md` — 架构分析 + ADR-001~005
- `doc/decision-questions.md` — 决策问题清单
- `doc/status-review.md` — 状态审视报告
- `doc/project-plan.md` — 本计划书
- `doc/deploy-guide.md` — 部署指南（Phase 4 产出）

## 八、评分覆盖矩阵

| 评分项 | 分值 | 覆盖模块 | 状态 |
|--------|------|----------|------|
| 功能完整度 | 40 | 数字人对话(20) + 数据大屏(10) + 感受度报告(10) | Phase 1+2 后具备 |
| 技术创新 | 30 | Dify工作流量化(15) + 旁路抓取架构(10) + 腾讯云ASR替代(5) | Phase 2 后具备 |
| 行业体验 | 20 | 景区知识库准确率(10) + 语音交互(5) + 大屏可视化(5) | Phase 1+3 后具备 |
| 文档 | 10 | 架构文档(4) + ADR(3) + 部署文档(3) | Phase 4 产出 |

## 九、时间估算

| 阶段 | 预计工时 | 累计 |
|------|----------|------|
| Phase 1：数字人部署 | 4 小时 | 4h |
| Phase 2：链路验证 | 4 小时 | 8h |
| Phase 3：数据展示修复 | 2 小时 | 10h |
| Phase 4：文档收尾 | 2 小时 | 12h |
| **合计** | **12 小时** | |

按每天 4-5 小时有效开发计算，**3 天可完成全部推进**。
