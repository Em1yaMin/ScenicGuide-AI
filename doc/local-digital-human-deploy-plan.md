# 本地数字人部署任务清单

> 目标：本地部署 `awesome-digital-human-live2d` 参考项目替代云端 light4ai，Vue3 访客端通过 iframe 嵌入本地数字人。
> ASR（语音转文字）走腾讯云一句话识别；TTS（文字转语音）走 Dify Workflow（插件接入）；Agent 走 DifyAgent。
> 生成时间：2026-07-13 · 更新：2026-07-13（补充腾讯云凭证 + Dify TTS Workflow Key）

## 一、目标架构

```
┌─────────────────────────────────────────────────────────────────┐
│  浏览器                                                          │
│                                                                  │
│  Vue3 访客端 (5173)          数字人前端 Next.js (3000)           │
│  ┌──────────────┐           ┌──────────────────────────┐        │
│  │ ChatView.vue │─iframe──→ │ Live2d + 录音 + 播放      │        │
│  └──────────────┘           └──────────┬───────────────┘        │
│                                        │ HTTP                    │
│                                        ▼                        │
│                             数字人后端 FastAPI (8001)            │
│                             ┌──────────────────────────┐        │
│                             │ ASR: 腾讯云一句话识别     │ ← 新增 │
│                             │ TTS: Dify TTS Workflow   │ ← 新增 │
│                             │ Agent: DifyAgent          │ 原生  │
│                             └──────────┬───────────────┘        │
│                                        │                        │
│  主项目后端 FastAPI (8000)             │                        │
│  ┌──────────────┐                      │                        │
│  │ polling      │←─拉对话──────────────┘  (Dify /conversations) │
│  │ analysis     │                                               │
│  └──────────────┘                                               │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
                   Dify 平台 (YOUR_DIFY_SERVER_IP)
                   ┌──────────────────────────┐
                   │ Chat Agent    (对话)      │  DIFY_API_KEY
                   │ Analysis WF   (量化分析)  │  app-YOUR_ANALYSIS_WF_KEY
                   │ TTS WF        (文字转语音)│  app-YOUR_TTS_WF_KEY
                   └──────────────────────────┘
```

## 二、端口规划

| 服务 | 端口 | 说明 |
|------|------|------|
| 主项目后端 FastAPI | 8000 | 已运行（digitalHumanApi） |
| 主项目前端 Vue3 | 5173 | 已运行（digitalHumanVue） |
| **数字人后端 FastAPI** | **8001** | 参考项目（避开 8000） |
| **数字人前端 Next.js** | **3000** | 参考项目（Vue iframe 目标） |

## 三、Dify 接口与 Key 清单

| 用途 | Dify 接口 | Key | 调用方 | 备注 |
|------|-----------|-----|--------|------|
| 对话 Agent | `/chat-messages` | `DIFY_API_KEY`（主项目同款） | DifyAgent | 数字人前端设置页填入 |
| 量化分析 Workflow | `/workflows/run` | `app-YOUR_ANALYSIS_WF_KEY` | 主项目 analysis | 已配置，已验证通过 |
| **TTS Workflow** | `/workflows/run` | **`app-YOUR_TTS_WF_KEY`** | 数字人后端 TTS 引擎 | 入参变量 `input`，输出 `files[]`（audio/mp3） |

### TTS Workflow 调用规格（已实测验证）

- 入参：`{"inputs":{"input":"要合成的话"}, "response_mode":"blocking", "user":"system"}`
- 返回：`data.outputs.files[0]`，含 `url`、`filename`、`mime_type=audio/mpeg`、`extension=.mp3`
- **注意**：返回的 `url` 是 `http://localhost/files/tools/xxx.mp3?...`，需把 `localhost` 替换为实际 Dify 地址 `YOUR_DIFY_SERVER_IP` 后才能下载
- 下载的 mp3 需用 pydub `mp3ToWav` 转换为 16k wav 交给前端播放（与原 difyTTS 一致）

> **与原生 difyTTS 的关键差异**：原 `difyTTS.py` 调 Chat Agent 的 `/text-to-audio`（直接返回二进制音频），而本 TTS Workflow 调 `/workflows/run`（返回文件元数据 + URL，需二次 HTTP 下载）。因此 **不能直接复用 difyTTS.py**，需新增 `difyTTSWorkflow.py` 引擎。

## 四、腾讯云 ASR 凭证

| 项 | 值 | 状态 |
|----|-----|------|
| SecretId | `AKID_YOUR_TENCENT_SECRET_ID` | ✅ 已验证（新建密钥，已成功调用一句话识别） |
| SecretKey | `YOUR_TENCENT_SECRET_KEY` | ✅ 已验证（同上） |
| AppID | 不再单独需要 | ✅ 一句话识别服务已开通，API 调用已验证通过 |
| 签名参考 | `doc/腾讯云语音识别/http调用模板.md` | Java 示例（本项目用 Python SDK，无需手写 TC3-HMAC-SHA256 签名） |

> **凭证验证记录**（2026-07-13）：用上述 SecretId/SecretKey 通过 `tencentcloud-sdk-python` 调用 `SentenceRecognition`（`EngSerViceType=16k_zh`）成功返回响应（`AudioDuration=2000ms`），证明凭证有效、服务已开通。原 `doc/腾讯云语音识别/api.txt` 记录的旧 SecretId `AKID_YOUR_OLD_TENCENT_SECRET_ID` 已废弃，以本文档为准。
>
> **安全提示**：SecretKey 是敏感凭证。若任务清单需要提交 git，应将其从本文档移除，改在运行时通过环境变量或数字人前端设置页填入。

### 腾讯云一句话识别关键参数（官方文档：[cloud.tencent.com/document/product/1093/35646](https://cloud.tencent.com/document/product/1093/35646)）

| 参数 | 取值 | 说明 |
|------|------|------|
| `EngSerViceType` | **`16k_zh`** | 引擎模型类型。16k 普通话通用（非电话场景）。**注意不是数字 `8`** |
| `SourceType` | `1` | 1=语音数据直接上传（base64） |
| `VoiceFormat` | `wav` | 音频格式，前端录制为 16k wav |
| `Data` | base64 编码的音频 |  |
| `DataLen` | 原始字节数 |  |
| `SubServiceType` | `2` | 2=一句话识别（SDK 可自动填） |
| `FilterPunc` | `1` | 过滤句末标点（可选） |
| 时长限制 | ≤60s，≤3MB |  |

> `EngSerViceType` 完整取值表见官方文档，常用：`16k_zh`(中文)、`16k_en`(英语)、`8k_zh`(电话中文)。前端 chatbot.tsx 录音采样率 16000，对应 `16k_zh`。

## 五、前置条件（需先准备）

| # | 项 | 当前状态 | 说明 |
|---|----|---------|------|
| P1 | ffmpeg | ✅ 已安装 | N-125551 (2026-07-12)，路径 `C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin`，已加入 PATH |
| P2 | pnpm | ✅ 已安装 | v11.12.0（npm install -g pnpm） |
| P3 | 腾讯云 SecretId | ✅ 已验证 | `AKID_YOUR_TENCENT_SECRET_ID` |
| P4 | 腾讯云 SecretKey | ✅ 已验证 | `YOUR_TENCENT_SECRET_KEY` |
| P5 | 腾讯云一句话识别服务 | ✅ 已开通 | API 调用已验证通过（返回 AudioDuration=2000ms） |
| P6 | Dify TTS Workflow Key | ✅ 已有 | `app-YOUR_TTS_WF_KEY` |
| P7 | Dify Chat Agent API Key | ✅ 已有 | 主项目 .env 的 DIFY_API_KEY，数字人前端复用 |
| P8 | DIFY_API_USER | ✅ EmiyaMin | 与 polling 的 DIFY_LIGHT4AI_USER 对齐 |

## 六、任务分阶段

### 阶段 1：环境与依赖准备

| # | 任务 | 命令/操作 | 验证标准 |
|---|------|----------|---------|
| 1.1 | ~~安装 ffmpeg~~ | ✅ 已完成 | `ffmpeg -version` → N-125551 |
| 1.2 | ~~安装 pnpm~~ | ✅ 已完成 | `pnpm --version` → 11.12.0 |
| 1.3 | 安装数字人后端依赖 | `cd 参考项目/.../awesome-digital-human-live2d-2.0.0 && pip install -r requirements.txt` | `python -c "import fastapi,uvicorn,websockets,edge_tts,pydub"` 成功 |
| 1.4 | 安装腾讯云 SDK | `pip install tencentcloud-sdk-python` | `python -c "import tencentcloud"` 成功 |
| 1.5 | 安装数字人前端依赖 | `cd web && pnpm install` | `web/node_modules` 生成 |

### 阶段 2：配置文件（端口 + 引擎 + Dify）

| # | 任务 | 文件 | 改动 |
|---|------|------|------|
| 2.1 | 创建运行配置 | `configs/config.yaml` | `cp config_all_in_dify.yaml config.yaml` |
| 2.2 | 改后端端口为 8001 | `configs/config.yaml` | `SERVER.PORT: 8001` |
| 2.3 | ASR 默认改腾讯云 | `configs/config.yaml` | `ENGINES.ASR.SUPPORT_LIST: ["difyAPI.yaml", "tencentAPI.yaml"]`，`DEFAULT: "tencentAPI.yaml"` |
| 2.4 | TTS 默认改 Workflow | `configs/config.yaml` | `ENGINES.TTS.SUPPORT_LIST: ["difyAPI.yaml", "difyWorkflowAPI.yaml"]`，`DEFAULT: "difyWorkflowAPI.yaml"` |
| 2.5 | 前端连后端 8001 | `web/.env` | `NEXT_PUBLIC_ADH_SERVER_PORT="8001"` |
| 2.6 | difyAgent user 对齐 | 前端启动后在设置页填 `DIFY_API_USER=EmiyaMin` | 见阶段 5 验证 |

### 阶段 3：新增腾讯云 ASR 引擎（核心开发）

参考项目用 Registry + Factory + Pool 架构，新增引擎只需配置 + 实现，装饰器自动注册。

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 3.1 | 新增引擎配置 | `configs/engines/asr/tencentAPI.yaml` | 见下方模板 |
| 3.2 | 新增引擎实现 | `digitalHuman/engine/asr/tencentASR.py` | 见下方模板，用 tencentcloud-sdk-python |
| 3.3 | 注册无需手动 | 自动通过 `@ASREngines.register("TencentAPI")` 装饰器注册 | import 时自动生效 |

**3.1 配置模板 `tencentAPI.yaml`：**

```yaml
NAME: "TencentAPI"
VERSION: "v0.0.1"
# SecretId/SecretKey 可在此填默认值，也可留空由前端设置页传入
# 已验证可用的凭证：
#   TENCENT_SECRET_ID:   AKID_YOUR_TENCENT_SECRET_ID
#   TENCENT_SECRET_KEY:  YOUR_TENCENT_SECRET_KEY
PARAMETERS: [
  { NAME: "TENCENT_SECRET_ID", DEFAULT: "" },
  { NAME: "TENCENT_SECRET_KEY", DEFAULT: "" }
]
```

**3.2 实现模板 `tencentASR.py`（一句话识别，仿 baiduASR 结构，用官方 SDK）：**

```python
# -*- coding: utf-8 -*-
from ..builder import ASREngines
from ..engineBase import BaseEngine
from typing import List, Optional
from digitalHuman.utils import AudioMessage, TextMessage, logger
import base64

__all__ = ["TencentAPI"]

@ASREngines.register("TencentAPI")
class TencentAPI(BaseEngine):
    def checkKeys(self) -> List[str]:
        return ["PARAMETERS"]

    async def run(self, input: AudioMessage, **kwargs) -> Optional[TextMessage]:
        try:
            from tencentcloud.asr.v20190614 import asr_client, models
            from tencentcloud.common import credential
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.common.profile.client_profile import ClientProfile

            SECRET_ID = kwargs.get("TENCENT_SECRET_ID", "")
            SECRET_KEY = kwargs.get("TENCENT_SECRET_KEY", "")
            for p in self.parameters():
                if p["NAME"] == "TENCENT_SECRET_ID" and not SECRET_ID:
                    SECRET_ID = p["DEFAULT"]
                if p["NAME"] == "TENCENT_SECRET_KEY" and not SECRET_KEY:
                    SECRET_KEY = p["DEFAULT"]

            cred = credential.Credential(SECRET_ID, SECRET_KEY)
            hp = HttpProfile(endpoint="asr.tencentcloudapi.com")
            cp = ClientProfile(httpProfile=hp)
            client = asr_client.AsrClient(cred, "", cp)

            req = models.SentenceRecognitionRequest()
            req.EngSerViceType = "16k_zh"       # 16k 普通话通用（前端采样率 16000）
            req.SourceType = 1                   # 1=语音数据直接上传
            req.VoiceFormat = str(input.format)  # wav/pcm/mp3
            req.Data = base64.b64encode(input.data).decode("utf-8")
            req.DataLen = len(input.data)
            req.UsrAudioKey = "digital-human"

            resp = client.SentenceRecognition(req)
            logger.debug(f"[ASR] Tencent response: {resp.Text}")
            return TextMessage(data=resp.Text)
        except Exception as e:
            logger.error(f"[ASR] Tencent run failed: {e}", exc_info=True)
            return None
```

> 注意：`EngSerViceType` 必须是字符串 `16k_zh`，不是数字 `8`（已在腾讯云官方文档核实）。

**验证 3：** 启动后端后，可手动 POST `/v0/infer`（engine=TencentAPI）测试 ASR。

### 阶段 4：新增 Dify TTS Workflow 引擎（核心开发）

原生 `difyTTS.py` 调 Chat Agent `/text-to-audio`（直接返回二进制），但本 TTS 是 Workflow（`/workflows/run` 返回文件 URL），不能复用，需新增引擎。

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| 4.1 | 新增引擎配置 | `configs/engines/tts/difyWorkflowAPI.yaml` | 见下方模板 |
| 4.2 | 新增引擎实现 | `digitalHuman/engine/tts/difyTTSWorkflow.py` | 见下方模板，调 Workflow + 下载 mp3 |
| 4.3 | 注册无需手动 | 自动通过 `@TTSEngines.register("DifyWorkflowAPI")` 装饰器注册 | import 时自动生效 |

**4.1 配置模板 `difyWorkflowAPI.yaml`：**

```yaml
NAME: "DifyWorkflowAPI"
VERSION: "v0.0.1"
PARAMETERS: [
  { NAME: "DIFY_API_URL", DEFAULT: "" },
  { NAME: "DIFY_WORKFLOW_API_KEY", DEFAULT: "" }
]
```

**4.2 实现模板 `difyTTSWorkflow.py`（调 Workflow /workflows/run + 下载音频文件）：**

```python
# -*- coding: utf-8 -*-
from ..builder import TTSEngines
from ..engineBase import BaseEngine
import asyncio
from typing import Optional
from digitalHuman.utils import httpxAsyncClient, AudioMessage, TextMessage, AudioFormatType
from digitalHuman.utils.audio import mp3ToWav
from digitalHuman.utils import logger

__all__ = ["DifyWorkflowAPI"]

@TTSEngines.register("DifyWorkflowAPI")
class DifyWorkflowAPI(BaseEngine):
    def setup(self):
        super().setup()
        self.asyncLock = True

    async def run(self, input: TextMessage, **kwargs) -> Optional[AudioMessage]:
        try:
            API_URL = kwargs.get("DIFY_API_URL", "")
            API_KEY = kwargs.get("DIFY_WORKFLOW_API_KEY", "")
            for p in self.parameters():
                if p["NAME"] == "DIFY_API_URL" and not API_URL:
                    API_URL = p["DEFAULT"]
                if p["NAME"] == "DIFY_WORKFLOW_API_KEY" and not API_KEY:
                    API_KEY = p["DEFAULT"]

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {API_KEY}'
            }
            payload = {
                "inputs": {"input": input.data},
                "response_mode": "blocking",
                "user": "adh",
            }

            logger.debug(f"[TTS] DifyWorkflow input: {input.data}")
            # 并发阻塞，防止 Dify 接口并发报错
            while not self.asyncLock:
                await asyncio.sleep(0.1)
            self.asyncLock = False

            # 1. 调 Workflow，拿文件 URL
            resp = await httpxAsyncClient.post(
                API_URL + "/workflows/run", json=payload, headers=headers
            )
            self.asyncLock = True
            if resp.status_code != 200:
                raise RuntimeError(f"workflow status_code: {resp.status_code}")

            files = resp.json().get("data", {}).get("outputs", {}).get("files", [])
            if not files:
                raise RuntimeError("TTS workflow returned no files")
            file_url = files[0].get("url", "")
            if not file_url:
                raise RuntimeError("TTS workflow file has no url")

            # 2. url 是 http://localhost/files/...，替换为实际 Dify 地址
            #    API_URL 形如 http://YOUR_DIFY_SERVER_IP/v1，提取 host
            from urllib.parse import urlparse
            host = urlparse(API_URL).scheme + "://" + urlparse(API_URL).hostname
            file_url = file_url.replace("http://localhost/", host + "/")

            # 3. 下载音频
            audio_resp = await httpxAsyncClient.get(file_url, timeout=60.0)
            if audio_resp.status_code != 200:
                raise RuntimeError(f"download audio failed: {audio_resp.status_code}")

            message = AudioMessage(
                data=mp3ToWav(audio_resp.content),
                desc=input.data,
                format=AudioFormatType.WAV,
                sampleRate=16000,
                sampleWidth=2,
            )
            return message
        except Exception as e:
            self.asyncLock = True
            logger.error(f"[TTS] DifyWorkflow run failed: {e}", exc_info=True)
            return None
```

> 已实测验证：TTS Workflow `app-YOUR_TTS_WF_KEY` 入参 `input`，返回 `files[0].url`，替换 `localhost→YOUR_DIFY_SERVER_IP` 后可下载 mp3（41460 bytes, audio/mpeg）。

**验证 4：** 启动后端后，可手动 POST `/v0/tts`（engine=DifyWorkflowAPI）测试 TTS。

### 阶段 5：启动服务

| # | 任务 | 命令 | 验证 |
|---|------|------|------|
| 5.1 | 启动数字人后端 | `python main.py`（在参考项目根目录） | 控制台显示 `Uvicorn running on 0.0.0.0:8001` |
| 5.2 | 启动数字人前端 | `cd web && pnpm run dev` | 控制台显示 `ready`，访问 http://localhost:3000 能打开 |
| 5.3 | 前端配置 Dify 参数 | 打开 localhost:3000，在设置页填：DIFY_API_URL、DIFY_API_KEY、DIFY_API_USER=EmiyaMin | 设置保存成功 |
| 5.4 | 前端配置 ASR 参数 | ASR 选 TencentAPI，填 TENCENT_SECRET_ID、TENCENT_SECRET_KEY | 设置保存成功 |
| 5.5 | 前端配置 TTS 参数 | TTS 选 DifyWorkflowAPI，填 DIFY_API_URL、DIFY_WORKFLOW_API_KEY=`app-YOUR_TTS_WF_KEY` | 设置保存成功 |

### 阶段 6：端到端验证

| # | 验证点 | 方法 | 预期 |
|---|--------|------|------|
| 6.1 | iframe 嵌入 | 打开 http://localhost:5173/visitor/chat | 数字人窗口加载 localhost:3000，不再"拒绝连接" |
| 6.2 | 语音识别 | 对麦克风说话 | 腾讯云 ASR 返回文字，数字人回复 |
| 6.3 | TTS 合成 | 数字人回复 | Dify TTS Workflow 返回音频，数字人开口说话 |
| 6.4 | 对话抓取 | 等待 30s 或 POST /api/v1/dialogue/sync-now | 主项目后端日志显示 `Polled N new messages`，对话入库 |
| 6.5 | user 对齐 | 查 Dify /conversations?user=EmiyaMin | 能拉到本地数字人产生的对话 |
| 6.6 | 量化分析 | POST /api/v1/analysis/run-now | 新对话被 Workflow 评估，大屏数据更新 |

## 七、风险与注意事项

| 风险 | 等级 | 应对 |
|------|------|------|
| ffmpeg 未装导致 wavToMp3/mp3ToWav 报错 | 高 | 必须先装 ffmpeg，否则 ASR/TTS 全链路不通 |
| ~~腾讯云 SecretKey 未获取~~ | ~~高~~ | ✅ 已获取并验证（见第四节凭证表） |
| 腾讯云 ASR 签名错误 | 中 | 用官方 `tencentcloud-sdk-python`，无需手写 TC3-HMAC-SHA256（`doc/腾讯云语音识别/http调用模板.md` 的 Java 示例仅供参考） |
| EngSerViceType 填错 | 中 | 必须用 `16k_zh`（字符串），不是数字 `8`。已核实官方文档 |
| TTS Workflow URL 是 localhost | 高 | 实现中已处理：`url.replace("http://localhost/", host+"/")` |
| 前端录音采样率与腾讯云要求不匹配 | 低 | 前端 chatbot.tsx 已配 16000，腾讯云 `16k_zh` 匹配 |
| iframe 跨域 | 低 | 参考项目 CORS 已设 `allow_origins=["*"]`（api.py:25） |
| 两个前端服务并存（5173+3000） | 低 | 端口不冲突，按规划运行 |
| Python 3.8 vs 参考 requirements 要求 3.10 | 中 | fastapi 0.105 支持 3.8，先试；若 pydantic/类型语法报错再升 3.10 |

## 八、文件改动清单（仅参考项目，不改主项目代码）

| 文件 | 操作 |
|------|------|
| `configs/config.yaml` | 新建（从 config_all_in_dify.yaml 复制并改端口/ASR/TTS） |
| `configs/engines/asr/tencentAPI.yaml` | 新建 |
| `digitalHuman/engine/asr/tencentASR.py` | 新建（腾讯云一句话识别） |
| `configs/engines/tts/difyWorkflowAPI.yaml` | 新建 |
| `digitalHuman/engine/tts/difyTTSWorkflow.py` | 新建（Dify TTS Workflow 引擎） |
| `web/.env` | 改 PORT 为 8001 |
| `requirements.txt` | 追加 `tencentcloud-sdk-python` |

> 主项目 Vue 前端 `.env` 的 `VITE_LIGHT4AI_URL=http://localhost:3000` 已预先配好，无需改。

## 九、参考文档

- 腾讯云一句话识别 API：https://cloud.tencent.com/document/product/1093/35646
- 腾讯云凭证文件：`doc/腾讯云语音识别/api.txt`（SecretId）
- 腾讯云签名参考：`doc/腾讯云语音识别/http调用模板.md`（Java 示例，本项目用 Python SDK 替代）
- Dify 访问账户：`doc/dify访问账户和数字人调用约定格式.md`
- 参考项目部署文档：`参考项目/awesome-digital-human-live2d-2.0.0/awesome-digital-human-live2d-2.0.0/docs/deploy_instrction.md`
