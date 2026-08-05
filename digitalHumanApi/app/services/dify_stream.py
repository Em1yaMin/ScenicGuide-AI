"""Dify Chat Agent 流式客户端（SSE 解析 + ThinkFilter 过滤）— 阶段1 T3。

双轨流式的 Dify 兜底链路：LangChain 链路故障回落时，以
`response_mode=streaming` 调用 Dify `/chat-messages`，逐行解析 SSE
`data: {json}` 事件，产出与 LangChain `chat_stream` 一致的 token/done 事件，
前端无需区分来源。

参考 live2d-frontend/digitalHuman/agent/core/difyAgent.py 的流式处理：

- 按行解析 `data: {json}`（`re` 提取，容忍行内前缀/空白）
- `ThinkFilter` 实时过滤 `<think>...</think>` 块（跨 chunk 标签分割安全），
  保证 TTS/展示不会读出模型思考内容
- `message`/`agent_message` 事件 → token 增量；`message_end` → done
  （携带 Dify 会话 id）；`error` 事件 → 抛错（由路由层转为 error 事件）

与 blocking 路径（dialogue._call_dify_chat）的区别：不重试——流式连接一旦
建立便无法安全重放（可能已向客户端发出部分事件），错误直接上抛。
"""
import json
import logging
import re
from typing import Any, AsyncIterator, Dict

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ThinkFilter:
    """流式过滤器：移除 `<think>...</think>` 块，处理跨 chunk 的标签分割。

    实现与 live2d-frontend 的 ThinkFilter 一致（仅去除注释差异）：
    维护跨 chunk 缓冲区，仅当缓冲区尾部可能构成不完整标签时才保留，
    其余内容立即透出，保证低延迟。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, text: str) -> str:
        """喂入文本，返回过滤后的输出。"""
        self._buffer += text
        output = ""
        while self._buffer:
            if self._in_think:
                end_idx = self._buffer.find("</think>")
                if end_idx != -1:
                    self._buffer = self._buffer[end_idx + 8 :]
                    self._in_think = False
                else:
                    if len(self._buffer) > 7:
                        self._buffer = self._buffer[-7:]
                    break
            else:
                start_idx = self._buffer.find("<think>")
                if start_idx != -1:
                    output += self._buffer[:start_idx]
                    self._buffer = self._buffer[start_idx + 7 :]
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


async def stream_dify_chat(
    query: str,
    user: str = "visitor",
    conversation_id: str = "",
) -> AsyncIterator[Dict[str, Any]]:
    """流式调用 Dify Chat Agent 的 /chat-messages 接口（response_mode=streaming）。

    Args:
        query: 用户消息（可能已包含 RAG 上下文）
        user: Dify 用户标识
        conversation_id: 会话 ID（空字符串表示新会话）

    Yields:
        {"type": "token", "text": str}                回答增量（已过滤 think 块）
        {"type": "done", "conversation_id": str}      流式完成（Dify 会话 id）

    Raises:
        RuntimeError: Dify 未配置、流式 error 事件、非 2xx 响应
        httpx 网络异常: 连接失败/超时（由路由层转为 error 事件）
    """
    if not settings.DIFY_API_URL or not settings.DIFY_API_KEY:
        raise RuntimeError("Dify API not configured")

    base_url = settings.DIFY_API_URL.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "streaming",
        "user": user,
        "conversation_id": conversation_id,
    }
    pattern = re.compile(r"data:\s*(\{.*\})")
    think_filter = ThinkFilter()
    final_conversation_id = ""

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(60.0),
    ) as client:
        async with client.stream("POST", "/chat-messages", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                match = pattern.search(line)
                if not match:
                    # 非 `data: {json}` 行（如 Dify 的注释/心跳纯文本），忽略
                    logger.debug("Dify streaming: 忽略非 SSE 数据行: %s", line[:80])
                    continue
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    logger.warning(
                        "Dify streaming: JSON 解析失败，跳过: %s", line[:80]
                    )
                    continue
                event = data.get("event", "")
                if event in ("message", "agent_message"):
                    text = think_filter.feed(data.get("answer", "") or "")
                    if text:
                        yield {"type": "token", "text": text}
                elif event == "message_end":
                    final_conversation_id = (
                        data.get("conversation_id") or final_conversation_id
                    )
                    remaining = think_filter.flush()
                    if remaining:
                        yield {"type": "token", "text": remaining}
                    yield {"type": "done", "conversation_id": final_conversation_id}
                    return
                elif event == "error":
                    raise RuntimeError(data.get("message") or "Dify 流式错误")
                # ping / agent_thought / message_replace 等事件忽略：
                # message_replace 是 agent 思考替换文本，逐字展示会产生跳动

    # 流正常结束但未收到 message_end（部分 Dify 版本/异常断开）：尽力收尾
    remaining = think_filter.flush()
    if remaining:
        yield {"type": "token", "text": remaining}
    yield {"type": "done", "conversation_id": final_conversation_id}
