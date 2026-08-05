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
from typing import List, Union
from digitalHuman.utils import httpxAsyncClient
from digitalHuman.utils import logger
from digitalHuman.utils import AudioMessage, TextMessage

__all__ = ["LangChainAgent"]

CHAT_ROUTE = "/api/v1/dialogue/chat"

# SSE 数据行：data: {json}；兼容无 "data: " 前缀的裸 JSON 行
DATA_PATTERN = re.compile(r'data:\s*({.*})')

# 后端流式事件类型（契约见服务端 /api/v1/dialogue/chat streaming 实现）
EVENT_CONVERSATION = "conversation"   # 携带 conversation_id 的会话事件
EVENT_MESSAGE = "message"             # 携带 answer 的文本增量事件
EVENT_STEP = "step"                   # agent 步骤事件（忽略/透出日志）
EVENT_ERROR = "error"                 # 携带 message 的错误事件
DONE_MARK = "[DONE]"                  # 流结束标记


@AGENTS.register("LangChainAgent")
class LangChainAgent(BaseAgent):
    """调用本后端 LangChain 对话链路的 Agent 引擎。

    请求 POST {LLM_API_URL}/api/v1/dialogue/chat（streaming=true 时后端返回 SSE）：
    - 主链路为本后端 LangChain agent（后端侧 DIALOGUE_ENGINE=langchain 默认，
      异常自动回落 Dify），ADH 侧只需对接这一个端点
    - SSE 事件契约：
        data: {"event": "conversation", "conversation_id": "xxx"}   # 会话事件（首帧）
        data: {"event": "message", "answer": "增量文本"}            # 文本增量
        data: {"event": "step", ...}                                 # 步骤事件（忽略）
        data: {"event": "error", "message": "..."}                   # 错误事件
        data: [DONE]                                                 # 流结束
    - 兼容无 event 的 {"answer": "..."} 形态；非流式响应为标准
      ChatResponse JSON（answer/conversation_id）
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
                        event_type = event.get("event", "")
                        if event_type == EVENT_ERROR:
                            raise RuntimeError(event.get("message", "unknown error"))
                        answer = event.get("answer", "")
                        if event_type == EVENT_STEP or not answer:
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
        """解析 SSE 行流，产出事件 dict（data: {json} 的 JSON 内容）。"""
        async for line in response.aiter_lines():
            chunkStr = line.strip()
            if not chunkStr or chunkStr == f"data: {DONE_MARK}" or chunkStr == DONE_MARK:
                continue
            chunkData = DATA_PATTERN.search(chunkStr)
            if not chunkData:
                logger.warning(f"[AGENT] Engine return truncated data: {chunkStr}")
                continue
            try:
                data = json.loads(chunkData.group(1))
            except json.JSONDecodeError:
                logger.warning(f"[AGENT] Engine return non-json data: {chunkStr}")
                continue
            if isinstance(data, dict):
                yield data
