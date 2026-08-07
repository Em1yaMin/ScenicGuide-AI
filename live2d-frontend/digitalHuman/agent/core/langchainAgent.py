# -*- coding: utf-8 -*-
'''
@File    :   langchainAgent.py
@Author  :   一力辉
'''

from ..builder import AGENTS
from ..agentBase import BaseAgent
from .difyAgent import ThinkFilter
import re
import json
from typing import Union
from digitalHuman.utils import httpxAsyncClient
from digitalHuman.utils import logger
from digitalHuman.utils import AudioMessage, TextMessage, StepMessage

__all__ = ["LangChainAgent"]

CHAT_ROUTE = "/api/v1/dialogue/chat"

# SSE 数据行：data: {json}；兼容无 "data: " 前缀的裸 JSON 行
DATA_PATTERN = re.compile(r'data:\s*({.*})')

# 后端流式事件类型（契约见服务端 app/routers/dialogue.py 的 _sse，阶段1 T3）：
#   {"type": "step", "tool", "label"}   工具调用步骤（忽略/透出日志）
#   {"type": "token", "text"}           回答增量
#   {"type": "done", "conversation_id", ...}  流式完成（会话 ID 在此事件）
#   {"type": "error", "message", "fallback"}  链路错误（fallback=true 已切换 Dify）
# 兼容旧契约（Dify 风格）：{"event": "message", "answer": ...}
EVENT_TOKEN = "token"         # 回答增量（text 字段）
EVENT_STEP = "step"           # agent 步骤事件（透出为 StepMessage）
EVENT_DONE = "done"           # 流式完成（携带 conversation_id）
EVENT_ERROR = "error"         # 携带 message 的错误事件
DONE_MARK = "[DONE]"          # 流结束标记（兼容）


@AGENTS.register("LangChainAgent")
class LangChainAgent(BaseAgent):
    """调用本后端 LangChain 对话链路的 Agent 引擎。

    请求 POST {LLM_API_URL}/api/v1/dialogue/chat（streaming=true 时后端返回 SSE）：
    - 主链路为本后端 LangChain agent（后端侧 DIALOGUE_ENGINE=langchain 默认，
      异常自动回落 Dify），ADH 侧只需对接这一个端点
    - SSE 事件契约（阶段1 T3，type 字段与 ChatResponse 对齐）：
        data: {"type": "step", "tool": "search_knowledge", "label": "正在检索知识库…"}
        data: {"type": "token", "text": "回答增量"}          # 逐字回答
        data: {"type": "done", "conversation_id": "xxx", ...}  # 流式完成（会话 ID）
        data: {"type": "error", "message": "...", "fallback": true}  # 错误（已回落 Dify）
    - 步骤可视化（阶段3，2026-08-07）：step 事件不再忽略，透出为
      StepMessage（tool/label）混入 run() 产出流，由 agentApi 编码为步骤帧
      供前端渲染；回答文本仍按原始字符串产出（ThinkFilter 过滤后），
      TTS/逐字渲染行为不变
    - 兼容旧形态 {"event": "message", "answer": "..."}（Dify 风格）
    - 非流式响应为标准 ChatResponse JSON（answer/conversation_id）
    """

    async def createConversation(self, streaming: bool, **kwargs) -> str:
        logger.debug(f"[AGENT] Engine create conversation streaming mode: {streaming}")
        try:
            self._validate(kwargs)
            API_URL = kwargs["LLM_API_URL"].rstrip("/")
            payload = self._build_payload("你好", streaming, **kwargs)
            headers = self._build_headers(kwargs)
            if streaming:
                async with httpxAsyncClient.stream('POST', API_URL + CHAT_ROUTE, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise RuntimeError(f"status_code: {response.status_code}, body: {body.decode('utf-8', 'ignore')[:200]}")
                    async for event in self._iter_sse(response):
                        if event.get("conversation_id"):
                            logger.debug(f"[AGENT] Engine create conversation response: {event['conversation_id']}")
                            return event["conversation_id"]
            else:
                response = await httpxAsyncClient.post(API_URL + CHAT_ROUTE, headers=headers, json=payload)
                if response.status_code != 200:
                    raise RuntimeError(f"status_code: {response.status_code}, body: {response.text[:200]}")
                data = response.json()
                if 'conversation_id' not in data:
                    logger.error(f"[AGENT] Engine create conversation failed: {data}")
                    return ""
                logger.debug(f"[AGENT] Engine create conversation response: {data['conversation_id']}")
                return data['conversation_id']
        except Exception as e:
            logger.error(f"[AGENT] Engine create conversation failed: {e}", exc_info=True)
            return ""
        return ""

    async def run(
        self,
        input: Union[TextMessage, AudioMessage],
        streaming: bool,
        **kwargs
    ):
        logger.debug(f"[AGENT] Engine run streaming mode: {streaming}")
        try:
            if isinstance(input, AudioMessage):
                raise RuntimeError("LangChainAgent does not support AudioMessage input yet")
            self._validate(kwargs)
            API_URL = kwargs["LLM_API_URL"].rstrip("/")
            payload = self._build_payload(input.data, streaming, **kwargs)
            headers = self._build_headers(kwargs)

            if streaming:
                thinkFilter = ThinkFilter()
                async with httpxAsyncClient.stream('POST', API_URL + CHAT_ROUTE, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        raise RuntimeError(f"status_code: {response.status_code}, body: {body.decode('utf-8', 'ignore')[:200]}")
                    async for event in self._iter_sse(response):
                        event_type = event.get("type", event.get("event", ""))
                        if event_type == EVENT_ERROR:
                            # fallback=true：主链路异常已切换 Dify，后端将继续
                            # 流出备用回答，仅提示不中断；fallback=false：双链路
                            # 均失败，抛错结束
                            if event.get("fallback") is not True:
                                raise RuntimeError(event.get("message", "unknown error"))
                            logger.warning(
                                f"[AGENT] 主链路异常已切换备用服务: {event.get('message', '')}"
                            )
                            continue
                        if event_type == EVENT_DONE:
                            # 流式完成：会话 ID 随 done 事件透出，供多轮对话续接
                            break
                        if event_type == EVENT_STEP:
                            # 工具调用步骤：透出 StepMessage 给调用方
                            # （agentApi 编码为步骤帧 → 前端渲染；回答
                            # 文本流不受影响，TTS 输入依赖不变）。
                            tool = event.get("tool", event.get("name", ""))
                            yield StepMessage(tool=tool, label=event.get("label", "") or tool)
                            continue
                        # 新契约 token/text 或旧契约 message/answer 均可
                        answer = event.get("text", event.get("answer", ""))
                        if not answer:
                            continue
                        logger.debug(f"[AGENT] Engine response: {event}")
                        filtered = thinkFilter.feed(answer)
                        if filtered:
                            yield filtered
                # 流结束后刷新残留
                remaining = thinkFilter.flush()
                if remaining:
                    yield remaining
            else:
                response = await httpxAsyncClient.post(API_URL + CHAT_ROUTE, headers=headers, json=payload)
                if response.status_code != 200:
                    raise RuntimeError(f"status_code: {response.status_code}, body: {response.text[:200]}")
                data = response.json()
                yield data.get('answer', "")
        except Exception as e:
            logger.error(f"[AGENT] Engine run failed: {e}", exc_info=True)
            yield "本后端对话接口请求返回错误。"

    # ---------- helpers ----------

    def _validate(self, kwargs: dict) -> None:
        """参数校验：parameters() 声明的参数必须全部提供。"""
        for paramter in self.parameters():
            if paramter['NAME'] not in kwargs:
                raise RuntimeError(f"Missing parameter: {paramter['NAME']}")

    @staticmethod
    def _build_headers(kwargs: dict) -> dict:
        headers = {'Content-Type': 'application/json'}
        token = kwargs.get("LLM_API_TOKEN", "")
        if token:
            headers['Authorization'] = f'Bearer {token}'
        return headers

    @staticmethod
    def _build_payload(query: str, streaming: bool, **kwargs) -> dict:
        """构造 /api/v1/dialogue/chat 请求体。"""
        payload = {
            "query": query,
            "streaming": streaming,
        }
        # conversation_id：前端设置页注入 settings["conversation_id"]（可能为空串）
        conversation_id = kwargs.get("conversation_id", "")
        if conversation_id:
            payload["conversation_id"] = conversation_id
        # 用户标识：默认 visitor（后端无 token 时使用）
        user = kwargs.get("LLM_API_USER", "")
        if user:
            payload["user"] = user
        # RAG 开关（前端 settings 均为字符串，做宽松布尔解析）
        use_rag = str(kwargs.get("LLM_USE_RAG", "true")).strip().lower()
        payload["use_rag"] = use_rag in ("true", "1", "yes", "on")
        try:
            top_k = int(kwargs.get("LLM_RAG_TOP_K", 3))
        except (TypeError, ValueError):
            top_k = 3
        payload["rag_top_k"] = max(1, min(10, top_k))
        return payload

    @staticmethod
    async def _iter_sse(response):
        """解析 SSE 行流，产出事件 dict（data: {json} 的 JSON 内容）。

        兼容非 SSE 阻塞响应：整行无法按 SSE 解析时，若为合法 ChatResponse JSON
        （含 answer 或 conversation_id 字段）则回退按事件产出，保证后端未实现
        流式端点时链路仍可用。
        """
        async for line in response.aiter_lines():
            chunkStr = line.strip()
            if not chunkStr or chunkStr == f"data: {DONE_MARK}" or chunkStr == DONE_MARK:
                continue
            chunkData = DATA_PATTERN.search(chunkStr)
            raw = chunkData.group(1) if chunkData else chunkStr
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"[AGENT] Engine return truncated data: {chunkStr}")
                continue
            if not isinstance(data, dict):
                logger.warning(f"[AGENT] Engine return truncated data: {chunkStr}")
                continue
            if not chunkData and "answer" not in data and "conversation_id" not in data:
                logger.warning(f"[AGENT] Engine return truncated data: {chunkStr}")
                continue
            yield data
