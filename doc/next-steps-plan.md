# 后续推进计划书（面向 arch 工作流）

> 编制日期：2026-07-13
> 项目：景区导览 AI 数字人（第十五届中国软件杯 A5）
> 前置工作流：`arch` runId 2026-07-13_09-08-27-8641（Phase 3 数据修复，validate fail + 9 blockers，design 重试中断）
> 产出文档：`doc/phase3-workflow-review.md`

## 一、项目现状

### 已完成

| 模块 | 状态 | 说明 |
|------|------|------|
| 架构设计 | ✅ | ADR-001~005，7 项核心决策 |
| 后端骨架 | ✅ | FastAPI 4 路由 + 2 后台任务 + 4 张表 |
| 前端骨架 | ✅ | Vue3 游客端 + 管理后台 |
| Dify API | ✅ | 双 Key + /messages/user/timestamp 修复 |
| ADR-004 回退 | ✅ | 删除推荐/景区同步/景点分析 |
| B: sentiment_trend | ✅ | Phase 3 已修复 → JOIN Dialogues + 7 天连续序列 |
| G: 占位 API | ✅ | Phase 3 已激活 → POST /sync-now + POST /run-now |
| 解析韧性 | ✅ (部分) | _parse_workflow_output 返回 Optional[Dict]，evaluate_dialogue 3 条重试路径，batch_evaluate commit try/except |

### 阻断问题（P0 — 来自 code-review）

| ID | 问题 | 位置 | 严重度 |
|----|------|------|--------|
| — | `_parse_workflow_output` 缺 `isinstance(result, dict)` 类型守卫 | `analysis.py:42` | 阻断（validate fail 根因） |
| I1 | `/run-now` 与 `analysis_loop` 并发调用 `batch_evaluate` → race + 评估丢失 | `analysis.py:10` / `main.py:43` | 阻断（部署时必定触发） |
| I3 | `docker-compose.yml` 缺 `DIFY_WORKFLOW_API_KEY` → evaluation 无限空转 | `docker-compose.yml:9-13` | 阻断（部署后静默失败） |

### 高优先级问题（P1 — 来自 code-review）

| ID | 问题 | 严重度 |
|----|------|--------|
| Q3 | `trigger_workflow` 缺 `_retry_silent`（GET 方法有，POST 没有） | 高 |
| Q4 | `_retry_silent` 的 `except Exception` 太宽 | 高 |
| Q5 | 不可解析对话无限重试，无 dead-letter | 高 |
| Q7 | polling commit 的 `except Exception` 替代了 IntegrityError | 高 |
| Q8 | `guardrails.json` 残留 `E:/e/` 路径（3 次复发） | 中 |

### 改进项（P2）

| ID | 问题 | 严重度 |
|----|------|--------|
| I2 | 非规范 sentiment label 静默丢弃 | 中 |
| I4 | poll_once 把 API 失败 `[]` 当 "无数据" | 中 |
| Q6 | `_parse_workflow_output` 无单元测试 | 低 |
| Q9 | GaugeChart 注册未使用 | 低 |

### 剩余项目工作

| 编号 | 内容 | 严重度 |
|------|------|--------|
| **Phase 1** | 部署参考项目（8001+3000），All-in-Dify + user=EmiyaMin | 阻塞 |
| **Phase 2** | polling + analysis 端到端跑通真实数据 | 阻塞 |
| **DB** | 清理 digital_human.db 残留 scenic_spots 表 | 低 |
| **I** | 补充架构现状文档 + 部署文档 | 低 |

## 二、分阶段推进计划

### 总览：三个工作流 Run

```
Run A: P0 修复 ──→ validate pass ──→ P1 加固
                                         │
Run B: Phase 1 部署 ──→ Phase 2 验证 ────┤
                                         │
Run C: P2 改进 + 清理 + 文档收尾 ─────────┘
                                         ↓
                                   演示视频录制
```

### Run A：Blocker 修复 + Retry 加固

**目标**：消除 code-review 全部 9 个 blocker，validate 拿到 pass
**预计**：1-2 天
**输入文件**：`doc/phase3-workflow-review.md` + code-review 报告

#### 子阶段 A1：P0 立即修复（半天）

| 步骤 | 内容 | 文件 | 验证标准 |
|------|------|------|----------|
| A1.1 | 补 `_parse_workflow_output` isinstance 类型守卫 | `digitalHumanApi/app/tasks/analysis.py` | `isinstance(result, dict)` 后 return，非 dict 返回 None |
| A1.2 | 加 `asyncio.Lock` 共享路由与 loop | `analysis.py` + `main.py` | lock 保护 `batch_evaluate()` 入口，两个调用方不并发 |
| A1.3 | docker-compose 补 `DIFY_WORKFLOW_API_KEY` | `docker-compose.yml` | 环境变量块含 `DIFY_WORKFLOW_API_KEY=${DIFY_WORKFLOW_API_KEY:-}` |
| A1.4 | analysis.py 区分 "key 缺失" vs "解析失败" | `analysis.py` | key 缺失 → skip 永久；解析失败 → retry |
| A1.5 | 重跑 `/skill:validate` | — | `verdict: pass` |

**A1.2 详细方案**：
- `main.py` 或 `analysis.py` 模块级别创建 `_eval_lock = asyncio.Lock()`
- `batch_evaluate()` 入口处 `async with _eval_lock:`
- 使 `/run-now` handler 和 `analysis_loop` 共享同一把锁
- 替代方案：外层 SELECT 加 `SELECT ... FOR UPDATE`（SQLite 暂不支持，不考虑）

#### 子阶段 A2：P1 Retry 加固（1 天）

| 步骤 | 内容 | 文件 | 验证标准 |
|------|------|------|----------|
| A2.1 | `trigger_workflow` 包裹 `_retry_silent`（3 次退避） | `digitalHumanApi/app/services/dify_client.py` | POST 与 GET 方法 retry 对称 |
| A2.2 | `_retry_silent` 收窄 `except Exception` | `dify_client.py` | 仅捕获传输异常，非传输异常 re-raise |
| A2.3 | Evaluation 模型加 `retry_count` 列，超 3 次跳过 | `models/evaluation.py` + `tasks/analysis.py` | 不可解析对话不永久重试 |
| A2.4 | polling commit 恢复 `IntegrityError` 具体捕获 | `digitalHumanApi/app/tasks/polling.py` | 恢复 `from sqlalchemy.exc import IntegrityError`，区分可重试 |
| A2.5 | 删除 `guardrails.json` 的 `E:/e/` 路径 | `.pi/extensions/guardrails.json` | 路径条数 = 正确条数；`test -d` 预提交 |

### Run B：Phase 1 部署 + Phase 2 验证

**目标**：数字人服务跑起来，全链路端到端通
**预计**：1 天
**前置**：Run A 完成

#### 子阶段 B1：数字人服务部署（Phase 1）

**ASR 决策**：采用腾讯云语音识别替代 Dify ASR。TTS 和 Agent 仍走 Dify（TTS 独立 Key `app-YOUR_TTS_WF_KEY`），因此基础配置用 `config_template.yaml`（多引擎混合模式）而非 `config_all_in_dify.yaml`。

##### B1-基础：环境与核心配置

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| B1.1 | 复制参考项目到 `digitalHumanLive2D/` | 目录结构完整 |
| B1.2 | 安装后端依赖：`pip install -r requirements.txt` | 无报错 |
| B1.3 | 安装 ffmpeg（`winget install ffmpeg` 或手动下载） | `ffmpeg -version` |
| B1.4 | 用 `config_template.yaml` 复制为 `config.yaml`，端口改 8001 | 保留多引擎模式 |
| B1.5 | config.yaml 中 TTS DEFAULT 置为 `difyAPI.yaml`，Agent DEFAULT 置为 `difyAgent.yaml` | TTS/Agent 走 Dify |
| B1.6 | `difyAgent.yaml` 的 `DIFY_API_USER` 改为 `EmiyaMin` | 与 polling 对齐 |

##### B1-ASR：腾讯云语音识别引擎接入（新增）

**引擎架构**：参考项目采用 Registry 注册 + Factory 工厂 + Pool 池化模式。新增引擎只需 3 个文件：
1. `configs/engines/asr/tencentASR.yaml` — 参数声明
2. `digitalHuman/engine/asr/tencentASR.py` — 引擎实现，用 `@ASREngines.register("TencentASR")` 注册
3. 在 `config.yaml` 的 `SERVER.ENGINES.ASR` 中注册并设为默认

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| B1.7 | 创建 `configs/engines/asr/tencentASR.yaml`，声明 `SECRET_ID` / `SECRET_KEY` / `APP_ID` 三个参数 | 配置文件就位 |
| B1.8 | 实现 `digitalHuman/engine/asr/tencentASR.py`：继承 `BaseEngine`，注册为 `TencentASR`，调用腾讯云实时语音识别 WebSocket API（`wss://asr.cloud.tencent.com/asr/v2/{APP_ID}`），返回 `TextMessage` | import 无报错 |
| B1.9 | config.yaml 中 ASR 的 `SUPPORT_LIST` 加入 `tencentASR.yaml`，`DEFAULT` 改为 `tencentASR.yaml` | EnginePool 加载成功 |

**B1.8 详细方案**：

```python
# digitalHuman/engine/asr/tencentASR.py 结构

from digitalHuman.engine.builder import ASREngines, BaseEngine
from digitalHuman.message import AudioMessage, TextMessage

@ASREngines.register("TencentASR")
class TencentASR(BaseEngine):
    async def run(self, input: AudioMessage, **kwargs) -> Optional[TextMessage]:
        # 1. 从 self.parameters() + kwargs 读取 SECRET_ID / SECRET_KEY / APP_ID
        # 2. 对音频数据做 base64 编码
        # 3. 通过 httpx/websockets 连接腾讯云 ASR WebSocket
        # 4. 发送音频分片，接收实时识别结果
        # 5. 返回 TextMessage(data=识别文本)
```

**技术要点**：
- 腾讯云实时语音识别 WebSocket 协议：`wss://asr.cloud.tencent.com/asr/v2/{APP_ID}`
- 需要 HMAC-SHA1 签名生成鉴权参数（参考腾讯云 ASR 文档的签名算法）
- 音频格式：16kHz 采样率、16bit、单声道 PCM/WAV
- 引擎参数配置与 difyASR 一致：`self.parameters()` 遍历读取，优先 `kwargs` 动态覆盖
- 前端的参数传递机制无需改动：`Comm.getInstance().asr(wavBlob, settings)` → settings 自动映射到 `**kwargs`

##### B1-前端：配置与验证

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| B1.10 | 启动后端：`python main.py` → 监听 8001，日志确认 EnginePool 加载 TencentASR | 无报错 |
| B1.11 | `cd web && pnpm install && pnpm run dev` → 3000 | 浏览器可打开 |
| B1.12 | 前端 Settings → Agent：填入 Dify API URL + Chat Agent Key | 配置保存 |
| B1.13 | 前端 Settings → TTS：填入 Dify TTS API Key（`app-YOUR_TTS_WF_KEY`） | 配置保存 |
| B1.14 | 前端 Settings → ASR：填入腾讯云 SecretId / SecretKey / AppId | 配置保存 |
| B1.15 | 文字对话测试 → 数字人回答 | 正常 |
| B1.16 | 语音测试（Ctrl+M 说话）→ 腾讯云 ASR 识别 → Dify Agent 回答 → Dify TTS 播放 | 语音链路通 |
| B1.17 | 改 Vue3 `.env`：`VITE_LIGHT4AI_URL=http://localhost:3000` | iframe 能加载数字人 |

**前置条件**：
- Dify Chat Agent 需开启「文字转语音」功能（TTS 走 Dify）
- 腾讯云账号需开通「语音识别」服务，获取 SecretId / SecretKey / AppId
- Dify Chat Agent 无需开启「语音转文字」（ASR 已改为腾讯云）

**风险**：
- 腾讯云 ASR WebSocket 的签名鉴权实现较复杂 → 可参考腾讯云 Python SDK 的 `tencentcloud-sdk-python` 或直接用 HMAC-SHA1 手写
- 音频格式不匹配 → 检查参考项目麦克风录制的采样率和格式（通常 16kHz mono PCM）

#### 子阶段 B2：核心数据链路验证（Phase 2）

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| B2.1 | 启动 FastAPI 业务后端（8000） | uvicorn 无报错 |
| B2.2 | 启动 Vue3 前端（5173） | 页面可打开 |
| B2.3 | 数字人对话 2-3 句 | 正常 |
| B2.4 | 等 30s，FastAPI 日志 | `Polled N new messages` |
| B2.5 | 查 SQLite dialogues 表 | 有真实对话 |
| B2.6 | 等 5min，analysis_loop 日志 | `Evaluated N dialogues` |
| B2.7 | 查 evaluations 表 | 有评分记录 |
| B2.8 | 大屏 + 报告页面 | 有数据展示 |
| B2.9 | `POST /sync-now` + `POST /run-now` 手动触发 | 返回正确计数 |

### Run C：改进清理 + 文档收尾

**目标**：P2 改进落地，文档完备
**预计**：1 天
**前置**：Run A 完成（可与 Run B 并行）

#### 子阶段 C1：P2 数据质量改进

| 步骤 | 内容 | 文件 | 验证标准 |
|------|------|------|----------|
| C1.1 | sentiment label 写入前校验 canonical 集 | `tasks/analysis.py` | normalize 小写，非规范 label log warning |
| C1.2 | poll_once 区分 `None`（失败）vs `[]`（空） | `services/dify_client.py` + `tasks/polling.py` | 失败返回 None，连续 N 次 escalate |
| C1.3 | 补 `_parse_workflow_output` 单元测试 6 场景 | `tests/test_analysis.py` | valid JSON / markdown fence / 非 dict / invalid / 空 / dict |
| C1.4 | 移除 GaugeChart 注册 | `digitalHumanVue/src/composables/useECharts.ts` | import 和 registration 均移除 |
| C1.5 | 清理 SQLite 残留 scenic_spots 表 | SQLite 数据库 | `.tables` 只有 4 张业务表 |

#### 子阶段 C2：文档收尾（Phase 4）

| 步骤 | 内容 | 验证标准 |
|------|------|----------|
| C2.1 | 更新 `doc/architecture-analysis.md`，追加 ADR-005 | 架构文档反映当前状态 |
| C2.2 | 更新 `doc/status-review.md`，标记所有已完成项 | 状态准确 |
| C2.3 | 编写 `doc/deploy-guide.md`（三个服务启动步骤） | 评委能照着跑 |
| C2.4 | 编写 `.env` 配置说明（每个 Key 的获取方式） | 清晰无误 |

## 三、服务启动顺序（日常开发）

```bash
# 1. Dify（服务器 YOUR_DIFY_SERVER_IP，已运行，无需操作）

# 2. 数字人后端（参考项目）
cd digitalHumanLive2D
python main.py     # → 8001

# 3. 数字人前端（参考项目）
cd digitalHumanLive2D/web
pnpm run dev       # → 3000

# 4. 业务后端（FastAPI）
cd digitalHumanApi
uvicorn app.main:app --reload --port 8000   # → 8000

# 5. 业务前端（Vue3）
cd digitalHumanVue
npm run dev        # → 5173
```

访问入口：`http://localhost:5173`（游客端 / 管理后台）

## 四、端口分配

| 服务 | 端口 | 说明 |
|------|------|------|
| Dify | 80 (远程) | YOUR_DIFY_SERVER_IP |
| 数字人后端 | 8001 | 参考项目 FastAPI |
| 数字人前端 | 3000 | 参考项目 Next.js |
| 业务后端 | 8000 | FastAPI（polling + analysis + API） |
| 业务前端 | 5173 | Vue3（游客端 + 管理后台） |

## 五、风险矩阵

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| P0.1: isinstance 守卫遗漏边界情况 | 低 | 中 | 补 Q6 单元测试覆盖 |
| I1: asyncio.Lock 死锁（路由+loop 竞争） | 低 | 中 | Lock 仅包裹 batch_evaluate 入口，内部无 await |
| I3: DIFY_WORKFLOW_API_KEY 在 .env 未配 | 中 | 高 | config.py 默认值给合理报错，而非 `""` |
| Q5: retry_count 列需 migration | 低 | 中 | SQLite 可用 ALTER TABLE ADD COLUMN |
| ffmpeg 安装失败（Windows） | 低 | 中 | `winget install ffmpeg` 或手动 zip |
| 腾讯云 ASR 签名鉴权超预期 | 中 | 高 | 优先用 `tencentcloud-sdk-python` ASR SDK；备选手写签名 |
| 腾讯云 ASR 音频格式不匹配 | 低 | 中 | 参考项目麦克风录制格式通常 16kHz mono PCM |
| Dify Chat Agent 未开启 TTS | 中 | 高 | 登录 Dify 后台确认 TTS 开关 |
| polling 拉不到对话 | 中 | 高 | 确认 user=EmiyaMin、Dify 有对话记录 |
| Workflow 输出格式不匹配 | 中 | 中 | 检查 Dify Workflow 输出 JSON，对齐 analysis.py 解析 |
| iframe CORS | 低 | 中 | 参考项目已设 `allow_origins=["*"]` |
| 演示时网络不稳定 | 低 | 高 | 本地缓存对话数据，大屏有兜底 |

## 六、关键文件索引

```
digitalHumanApi/app/tasks/analysis.py          # P0.1 + I1 + Q5 修改
digitalHumanApi/app/tasks/polling.py           # Q7 + I4 修改
digitalHumanApi/app/services/dify_client.py    # Q3 + Q4 + I4 修改
digitalHumanApi/app/services/admin_service.py  # I2 上游（已修完）
digitalHumanApi/app/models/evaluation.py       # Q5: 加 retry_count 列
digitalHumanApi/tests/test_analysis.py         # Q6: 补 6 场景测试
digitalHumanVue/src/views/DashboardView.vue    # 已修完（.map() 动态驱动）
digitalHumanVue/src/composables/useECharts.ts  # Q9: 移除 GaugeChart
docker-compose.yml                             # I3: 补 DIFY_WORKFLOW_API_KEY
.pi/extensions/guardrails.json                 # Q8: 删 E:/e/ 路径
```

## 七、执行顺序与时间估算

| 阶段 | 预计工时 | 依赖 | 说明 |
|------|----------|------|------|
| A1: P0 修复 | 3h | 无 | 立即开始，修完 validate |
| A2: P1 加固 | 5h | A1 | retry 链路 + guardrails 清理 |
| B1: Phase 1 部署 | 4h | A1 完成 | 可与 A2 并行 |
| B2: Phase 2 验证 | 2h | B1 + A2 | 全链路跑通 |
| C1: P2 改进 | 2h | 无 | 可与 A2/B1 并行 |
| C2: 文档收尾 | 2h | B2 | 部署文档在经验之后写更准确 |
| 演示视频 | 2h | B2 | 录制 + 剪辑 |
| **合计** | **20h** | | 约 3-4 天 |

## 八、工作流消费指引

本文件设计为 arch 工作流（`wf` 命令）的输入。工作流将按 `research → design → plan → implement → validate → code-review` 循环执行。

**推荐执行方式**（分批触发，每批一个独立 run）：

```
# 第一批：P0 修复（最小集，快速闭环）
输入本文件 + doc/phase3-workflow-review.md
目标：validate pass, 关闭 3 个 P0 blocker

# 第二批：P1 加固
输入本文件 + code-review 报告
目标：关闭 5 个 P1 blocker

# 第三批：P2 改进 + 清理 + 文档
输入本文件
目标：关闭 4 个 P2 + 扫尾
```

> **注意**：Phase 1 部署（12 步操作）是手动的系统运维，不适合 arch 工作流的代码修改闭环。建议在 P0 修复完成后手动执行 B1 步骤，然后触发第二批工作流做 P1 加固。B2 端到端验证在 P1 加固 + B1 部署都完成后手动执行。
