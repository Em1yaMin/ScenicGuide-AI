# -*- coding: utf-8 -*-
'''
@File    :   test_langchain_agent.py
@Author  :   一力辉
LangChainAgent 引擎单测：mock 掉 httpxAsyncClient，验证
- SSE 流式解析（message 增量 / step 忽略 / conversation 事件）
- 非流式 JSON 响应
- 请求体构造（streaming/use_rag/rag_top_k/conversation_id/token）
- 异常路径（HTTP 错误 / error 事件 / 参数缺失）
'''

import json
import os
import pytest
from httpx import AsyncClient

from digitalHuman.agent.core import langchainAgent as langchainAgent_mod
from digitalHuman.agent.core.langchainAgent import LangChainAgent
from digitalHuman.utils import TextMessage
from digitalHuman.utils.configParser import CONFIG_ROOT_PATH, parseConfig

ENGINE = "LangChainAgent"
CHAT_URL = "http://127.0.0.1:8000/api/v1/dialogue/chat"


@pytest.fixture(scope="session")
def agent() -> LangChainAgent:
    """按 langchainAgent.yaml 配置实例化引擎（不联网）。"""
    cfg = parseConfig(os.path.join(CONFIG_ROOT_PATH, "agents", "langchainAgent.yaml"))
    return LangChainAgent(cfg)


# ---------- 网络 mock ----------

class FakeResponse:
    """模拟 httpx 流式/普通响应。"""
    def __init__(self, lines=None, status_code=200, text="", body=None):
        self.lines = lines or []
        self.status_code = status_code
        self.text = text
        if body is None:
            body = text if isinstance(text, bytes) else text.encode("utf-8")
        self.body = body

    def json(self):
        return json.loads(self.text)

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aread(self):
        return self.body


class _StreamCtx:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class FakeClient:
    """记录请求参数并返回预设响应的假 httpxAsyncClient。"""
    def __init__(self, response):
        self.response = response
        self.requests = []

    def stream(self, method, url, headers=None, json=None):
        self.requests.append({"method": method, "url": url, "headers": headers, "json": json})
        return _StreamCtx(self.response)

    async def post(self, url, headers=None, json=None):
        self.requests.append({"method": "POST", "url": url, "headers": headers, "json": json})
        return self.response


@pytest.fixture
def fake_client(monkeypatch):
    """默认返回 200 空流的假 client。"""
    client = FakeClient(FakeResponse(lines=[]))
    monkeypatch.setattr(langchainAgent_mod, "httpxAsyncClient", client)
    return client


def sse_lines(*events):
    """构造 SSE 行。"""
    return [f"data: {json.dumps(e)}" for e in events] + ["data: [DONE]"]


# ---------- 参数构造 ----------

def settings(**overrides):
    base = {
        "LLM_API_URL": "http://127.0.0.1:8000",
        "LLM_API_TOKEN": "",
        "LLM_API_USER": "visitor",
        "LLM_USE_RAG": "true",
        "LLM_RAG_TOP_K": "3",
    }
    base.update(overrides)
    return base


# ---------- API 级测试（/adh/agent/v0/infer） ----------

class Test_LangChainAgent_API:
    @pytest.mark.asyncio(scope="session")
    async def test_list_contains(self, version: str, client: AsyncClient):
        """引擎注册并出现在 agent 列表。"""
        url = f"/adh/agent/{version}/list"
        resp = await client.get(url)
        assert resp.status_code == 200
        resp = resp.json()
        assert resp["code"] == 0
        assert ENGINE in resp["data"]

    @pytest.mark.asyncio(scope="session")
    async def test_stream_infer(self, version: str, client: AsyncClient, fake_client):
        """SSE 流式：message 事件增量拼接，step 事件忽略。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"event": "conversation", "conversation_id": "abc123"},
            {"event": "step", "name": "search_spots"},
            {"event": "message", "answer": "你好，"},
            {"event": "message", "answer": "欢迎来到景区"},
            {"event": "done"},
        ))
        url = f"/adh/agent/{version}/infer"
        item = {"engine": ENGINE, "settings": settings(), "streaming": True, "data": "你好"}
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        assert "你好，欢迎来到景区" in resp.text
        # 请求体校验
        req = fake_client.requests[0]
        assert req["method"] == "POST"
        assert req["url"] == CHAT_URL
        assert req["json"]["streaming"] is True
        assert req["json"]["query"] == "你好"
        assert req["json"]["use_rag"] is True
        assert req["json"]["rag_top_k"] == 3

    @pytest.mark.asyncio(scope="session")
    async def test_blocking_infer(self, version: str, client: AsyncClient, fake_client):
        """非流式：标准 ChatResponse JSON。"""
        fake_client.response = FakeResponse(text=json.dumps({
            "answer": "完整回复",
            "conversation_id": "cid-1",
            "rag_context": "",
            "rag_sources": [],
        }))
        url = f"/adh/agent/{version}/infer"
        item = {"engine": ENGINE, "settings": settings(), "streaming": False, "data": "你好"}
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        assert "完整回复" in resp.text
        req = fake_client.requests[0]
        assert req["json"]["streaming"] is False

    @pytest.mark.asyncio(scope="session")
    async def test_stream_blocking_json_fallback(self, version: str, client: AsyncClient, fake_client):
        """后端未实现流式：streaming=True 但响应为整行阻塞 ChatResponse JSON，回退产出 answer。"""
        fake_client.response = FakeResponse(lines=[json.dumps({
            "answer": "阻塞式完整回复",
            "conversation_id": "cid-block",
            "rag_context": "",
            "rag_sources": [],
        })])
        url = f"/adh/agent/{version}/infer"
        item = {"engine": ENGINE, "settings": settings(), "streaming": True, "data": "你好"}
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        assert "阻塞式完整回复" in resp.text
        assert fake_client.requests[0]["json"]["streaming"] is True

    @pytest.mark.asyncio(scope="session")
    async def test_stream_blocking_conversation_id(self, version: str, client: AsyncClient, fake_client):
        """后端未实现流式：streaming=True 但整行阻塞 JSON 含 conversation_id，回退返回会话 ID。"""
        fake_client.response = FakeResponse(lines=[json.dumps({
            "answer": "你好",
            "conversation_id": "cid-block",
        })])
        url = f"/adh/agent/{version}/conversation_id"
        item = {"engine": ENGINE, "settings": settings(), "streaming": True}
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        resp = resp.json()
        assert resp["code"] == 0
        assert resp["data"] == "cid-block"

    @pytest.mark.asyncio(scope="session")
    async def test_stream_conversation_id(self, version: str, client: AsyncClient, fake_client):
        """createConversation：从 SSE conversation 事件取会话 ID。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"event": "conversation", "conversation_id": "conv-42"},
            {"event": "message", "answer": "你好"},
            {"event": "done"},
        ))
        url = f"/adh/agent/{version}/conversation_id"
        item = {"engine": ENGINE, "settings": settings(), "streaming": True}
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        resp = resp.json()
        assert resp["code"] == 0
        assert resp["data"] == "conv-42"
        assert fake_client.requests[0]["json"]["query"] == "你好"

    @pytest.mark.asyncio(scope="session")
    async def test_http_error_yields_message(self, version: str, client: AsyncClient, fake_client):
        """后端 500：流式响应体应包含错误提示而非崩溃。"""
        fake_client.response = FakeResponse(status_code=500, body=b"internal error")
        url = f"/adh/agent/{version}/infer"
        item = {"engine": ENGINE, "settings": settings(), "streaming": True, "data": "你好"}
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        assert "错误" in resp.text


# ---------- 引擎级测试 ----------

class Test_LangChainAgent_Engine:
    @pytest.mark.asyncio(scope="session")
    async def test_think_block_filtered(self, fake_client, agent):
        """流式 <think> 块被过滤，不输出思考内容。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"event": "message", "answer": "<think>内部推理"},
            {"event": "message", "answer": "</think>最终回答"},
            {"event": "done"},
        ))
        outputs = [text async for text in agent.run(TextMessage(data="你好"), True, **settings())]
        assert "".join(outputs) == "最终回答"

    # ---------- 新契约（type: token/step/done，阶段1 T3 实测，2026-08-06 修正） ----------

    @pytest.mark.asyncio(scope="session")
    async def test_new_contract_tokens_assembled(self, fake_client, agent):
        """type=token 增量拼接、step 忽略、done 结束（本后端实际 SSE 契约）。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"type": "step", "tool": "search_spots", "label": "正在查询景点…"},
            {"type": "token", "text": "你好，"},
            {"type": "token", "text": "欢迎来到景区"},
            {"type": "done", "conversation_id": "conv-new", "rag_context": "", "rag_sources": []},
        ))
        outputs = [text async for text in agent.run(TextMessage(data="你好"), True, **settings())]
        assert "".join(outputs) == "你好，欢迎来到景区"

    @pytest.mark.asyncio(scope="session")
    async def test_new_contract_conversation_id_from_done(self, fake_client, agent):
        """createConversation：会话 ID 随 done 事件透出（新契约无独立 conversation 事件）。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"type": "step", "tool": "search_knowledge", "label": "正在检索知识库…"},
            {"type": "token", "text": "你好"},
            {"type": "done", "conversation_id": "conv-new-42", "rag_context": "", "rag_sources": []},
        ))
        cid = await agent.createConversation(streaming=True, **settings())
        assert cid == "conv-new-42"

    @pytest.mark.asyncio(scope="session")
    async def test_new_contract_error_fallback_true_continues(self, fake_client, agent):
        """error(fallback=true)：仅告警不中断，继续消费 Dify 兜底流式输出。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"type": "error", "message": "主链路异常，已自动切换备用服务。", "fallback": True},
            {"type": "token", "text": "备用回答"},
            {"type": "done", "conversation_id": "dify-cid", "rag_context": "", "rag_sources": []},
        ))
        outputs = [text async for text in agent.run(TextMessage(data="你好"), True, **settings())]
        assert "".join(outputs) == "备用回答"

    @pytest.mark.asyncio(scope="session")
    async def test_new_contract_error_fallback_false_yields_error(self, fake_client, agent):
        """error(fallback=false)：双链路均失败，产出错误提示文本。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"type": "error", "message": "抱歉，AI 服务暂时不可用", "fallback": False},
        ))
        outputs = [text async for text in agent.run(TextMessage(data="你好"), True, **settings())]
        assert "错误" in "".join(outputs)

    @pytest.mark.asyncio(scope="session")
    async def test_conversation_id_forwarded(self, fake_client, agent):
        """settings 中的 conversation_id 透传到请求体。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"event": "conversation", "conversation_id": "cid-keep"},
            {"event": "message", "answer": "继续"},
            {"event": "done"},
        ))
        outputs = [text async for text in agent.run(
            TextMessage(data="再说说"), True, **settings(conversation_id="cid-keep"))]
        assert "".join(outputs) == "继续"
        assert fake_client.requests[0]["json"]["conversation_id"] == "cid-keep"

    @pytest.mark.asyncio(scope="session")
    async def test_use_rag_string_false(self, fake_client, agent):
        """字符串 "false" 的 LLM_USE_RAG 解析为 False；非法 top_k 回退默认。"""
        fake_client.response = FakeResponse(lines=sse_lines({"event": "done"}))
        outputs = [text async for text in agent.run(
            TextMessage(data="hi"), True,
            **settings(LLM_USE_RAG="false", LLM_RAG_TOP_K="abc"))]
        assert outputs == []
        req = fake_client.requests[0]["json"]
        assert req["use_rag"] is False
        assert req["rag_top_k"] == 3

    @pytest.mark.asyncio(scope="session")
    async def test_error_event_raises(self, fake_client, agent):
        """error 事件 → 生成错误提示文本。"""
        fake_client.response = FakeResponse(lines=sse_lines(
            {"event": "error", "message": "LLM 未配置"},
            {"event": "done"},
        ))
        outputs = [text async for text in agent.run(TextMessage(data="你好"), True, **settings())]
        assert "错误" in "".join(outputs)

    @pytest.mark.asyncio(scope="session")
    async def test_blocking_json_think_filtered(self, fake_client, agent):
        """阻塞 JSON 回退：整行 ChatResponse JSON 的 answer 产出且 <think> 被过滤。"""
        fake_client.response = FakeResponse(lines=[json.dumps({
            "answer": "<think>内部推理</think>最终回答",
            "conversation_id": "cid-block",
        })])
        outputs = [text async for text in agent.run(TextMessage(data="你好"), True, **settings())]
        assert "".join(outputs) == "最终回答"

    @pytest.mark.asyncio(scope="session")
    async def test_truncated_line_ignored(self, fake_client, agent):
        """非 SSE 且非合法 ChatResponse JSON 的行被忽略，后续有效行正常产出。"""
        fake_client.response = FakeResponse(lines=[
            "some random text",
            json.dumps({"answer": "有效回复"}),
        ])
        outputs = [text async for text in agent.run(TextMessage(data="你好"), True, **settings())]
        assert "".join(outputs) == "有效回复"

    @pytest.mark.asyncio(scope="session")
    async def test_missing_parameter(self, fake_client, agent):
        """缺少必填参数 → 生成错误提示文本，且不发请求。"""
        outputs = [text async for text in agent.run(
            TextMessage(data="你好"), True, LLM_API_URL="http://127.0.0.1:8000")]
        assert "错误" in "".join(outputs)
        assert fake_client.requests == []

    @pytest.mark.asyncio(scope="session")
    async def test_token_in_header(self, fake_client, agent):
        """LLM_API_TOKEN 配置时写入 Authorization Bearer。"""
        fake_client.response = FakeResponse(lines=sse_lines({"event": "done"}))
        outputs = [text async for text in agent.run(
            TextMessage(data="你好"), True, **settings(LLM_API_TOKEN="jwt-token"))]
        assert outputs == []
        headers = fake_client.requests[0]["headers"]
        assert headers["Authorization"] == "Bearer jwt-token"
