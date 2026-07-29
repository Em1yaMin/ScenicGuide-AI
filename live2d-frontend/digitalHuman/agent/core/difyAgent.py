# -*- coding: utf-8 -*-
'''
@File    :   difyAgnet.py
@Author  :   一力辉 
'''

from ..builder import AGENTS
from ..agentBase import BaseAgent
import re
import json
from typing import List, Union
from digitalHuman.utils import httpxAsyncClient
from digitalHuman.utils import logger
from digitalHuman.utils import AudioMessage, TextMessage

__all__ = ["DifyAgent"]


class ThinkFilter:
    """流式过滤器：移除 <think>...</think> 块，处理跨 chunk 的标签分割。"""
    def __init__(self):
        self._buffer = ""
        self._in_think = False

    def feed(self, text: str) -> str:
        """喂入文本，返回过滤后的输出。"""
        self._buffer += text
        output = ""
        while self._buffer:
            if self._in_think:
                end_idx = self._buffer.find('</think>')
                if end_idx != -1:
                    self._buffer = self._buffer[end_idx + 8:]
                    self._in_think = False
                else:
                    if len(self._buffer) > 7:
                        self._buffer = self._buffer[-7:]
                    break
            else:
                start_idx = self._buffer.find('<think>')
                if start_idx != -1:
                    output += self._buffer[:start_idx]
                    self._buffer = self._buffer[start_idx + 7:]
                    self._in_think = True
                else:
                    safe_len = max(0, len(self._buffer) - 6)
                    output += self._buffer[:safe_len]
                    self._buffer = self._buffer[safe_len:]
                    break
        return output

    def flush(self) -> str:
        """流结束后获取残留缓冲区（不处于 think 内时才返回）。"""
        if self._in_think:
            return ""
        return self._buffer


@AGENTS.register("DifyAgent")
class DifyAgent(BaseAgent):

    async def createConversation(self, streaming: bool, **kwargs) -> str:
        logger.debug(f"[AGENT] Engine create conversation streaming mode: {streaming}")
        try:
            if isinstance(input, AudioMessage):
                raise RuntimeError("DifyAgent does not support AudioMessage input yet")
            # 参数校验
            for paramter in self.parameters():
                if paramter['NAME'] not in kwargs:
                    raise RuntimeError(f"Missing parameter: {paramter['NAME']}")
            API_URL = kwargs["DIFY_API_URL"]
            API_KEY = kwargs["DIFY_API_KEY"]
            API_USER = kwargs["DIFY_API_USER"]
            conversation_id = kwargs["conversation_id"] if "conversation_id" in kwargs else ""
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {API_KEY}'
            }
            responseMode = "streaming" if streaming else "blocking"
            payload = {
                "inputs": {},
                "query": "你好",
                "response_mode": responseMode,
                "user": API_USER,
                "conversation_id": conversation_id,
                "files":[]
            }

            pattern = re.compile(r'data:\s*({.*})')
            if streaming:
                async with httpxAsyncClient.stream('POST', API_URL + "/chat-messages", headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"status_code: {response.status_code}")
                    async for chunk in response.aiter_lines():
                        chunkStr = chunk.strip()
                        if not chunkStr: continue
                        chunkData = pattern.search(chunkStr)
                        # 部分dify返回不完整，该模板匹配会失效
                        if not chunkStr.endswith('}') or not chunkData: 
                            logger.warning(f"[AGENT] Engine return truncated data: {chunkStr}")
                            continue
                        chunkData = chunkData.group(1)

                        data = json.loads(chunkData)
                        # 处理流式返回字符串
                        if 'conversation_id' not in data:
                            logger.error(f"[AGENT] Engine create conversation failed: {data}")
                            return ""
                        else:
                            logger.debug(f"[AGENT] Engine create conversation response: {data['conversation_id']}")
                            return data['conversation_id']

            else:
                response = await httpxAsyncClient.post(API_URL + "/chat-messages", headers=headers, json=payload)
                data = json.loads(response.text)
                if 'conversation_id' not in data:
                    logger.error(f"[AGENT] Engine create conversation failed: {data}")
                    return ""
                return data['conversation_id']
        except Exception as e:
            logger.error(f"[AGENT] Engine create conversation failed: {e}", exc_info=True)
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
                raise RuntimeError("DifyAgent does not support AudioMessage input yet")
            # 参数校验
            for paramter in self.parameters():
                if paramter['NAME'] not in kwargs:
                    raise RuntimeError(f"Missing parameter: {paramter['NAME']}")
            API_URL = kwargs["DIFY_API_URL"]
            API_KEY = kwargs["DIFY_API_KEY"]
            API_USER = kwargs["DIFY_API_USER"]
            conversation_id = kwargs["conversation_id"] if "conversation_id" in kwargs else ""
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {API_KEY}'
            }
            responseMode = "streaming" if streaming else "blocking"
            payload = {
                "inputs": {},
                "query": input.data,
                "response_mode": responseMode,
                "user": API_USER,
                "conversation_id": conversation_id,
                "files":[]
            }

            pattern = re.compile(r'data:\s*({.*})')
            if streaming:
                thinkFilter = ThinkFilter()
                async with httpxAsyncClient.stream('POST', API_URL + "/chat-messages", headers=headers, json=payload) as response:
                    async for chunk in response.aiter_lines():
                        chunkStr = chunk.strip()
                        if not chunkStr: continue
                        chunkData = pattern.search(chunkStr)
                        # 部分dify返回不完整，该模板匹配会失效
                        if not chunkStr.endswith('}') or not chunkData: 
                            logger.warning(f"[AGENT] Engine return truncated data: {chunkStr}")
                            continue
                        chunkData = chunkData.group(1)

                        try:
                            data = json.loads(chunkData)
                            # 处理流式返回字符串
                            if "message" in data["event"]:
                                if 'answer' in data:
                                    logger.debug(f"[AGENT] Engine response: {data}")
                                    filtered = thinkFilter.feed(data['answer'])
                                    if filtered:
                                        yield filtered
                        except Exception as e:
                            logger.error(f"[AGENT] Engine run failed: {e}", exc_info=True)
                            yield "内部错误，请检查dify信息。"
                # 流结束后刷新残留
                remaining = thinkFilter.flush()
                if remaining:
                    yield remaining

            else:
                response = await httpxAsyncClient.post(API_URL + "/chat-messages", headers=headers, json=payload)
                data = json.loads(response.text)
                yield data['answer']
        except Exception as e:
            logger.error(f"[AGENT] Engine run failed: {e}", exc_info=True)
            yield "dify接口请求返回错误。"