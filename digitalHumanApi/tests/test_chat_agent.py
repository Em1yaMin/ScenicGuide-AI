"""LangChain 对话 Agent（app/services/chat_agent.py）单测。

覆盖：工具集可调用（知识检索/景点/路线/推荐）、agent 完整工具调用链路
（httpx.MockTransport 模拟 DeepSeek 系 tool-calling 响应，不发真实请求）、
多轮上下文（进程内会话）、RAG 来源透出、use_rag 开关、空回答兜底。
"""

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services import chat_agent

PRIMARY = {
    "LLM_API_BASE": "https://opencode.ai/zen/go/v1",
    "LLM_API_KEY": "sk-primary-test",
    "LLM_MODEL": "deepseek-v4-flash",
}
FALLBACK = {
    "LLM_FALLBACK_API_BASE": "https://api.deepseek.com/v1",
    "LLM_FALLBACK_API_KEY": "sk-fallback-test",
    "LLM_FALLBACK_MODEL": "deepseek-chat",
}


@pytest.fixture(autouse=True)
def _pin_llm_config(monkeypatch):
    """固定主/备用端点配置，避免测试依赖真实环境变量。"""
    for key, value in {**PRIMARY, **FALLBACK}.items():
        monkeypatch.setattr(settings, key, value)
    # 重置 agent 缓存与进程内会话，避免跨测试污染
    chat_agent.reset_agent_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._generated_conversation_ids.clear()
    yield
    chat_agent.reset_agent_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._generated_conversation_ids.clear()


# ── Mock 响应构造（OpenAI/DeepSeek 兼容 tool-calling 格式） ───────────────


def _completion(choices):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": PRIMARY["LLM_MODEL"],
        "choices": choices,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _tool_call_choice(name: str, args: dict, call_id: str = "call_1"):
    return {
        "index": 0,
        "finish_reason": "tool_calls",
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }
            ],
        },
    }


def _text_choice(text: str):
    return {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": text},
    }


def _llm_with_mock(handler) -> "object":
    """构造注入 MockTransport 的 ChatOpenAI（经 llm.py 工厂，验证完整链路）。

    agent 经 ainvoke 调用时走 openai SDK 的 async client，因此同步/异步
    两个 httpx client 都要注入 mock transport（http_async_client 参数名，
    见 langchain-openai ChatOpenAI 字段定义）。
    """
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


def _mock_agent(handler, *, use_rag: bool):
    """用 mock LLM 构建 agent 实例（绕过模块级缓存，供 chat() 测试注入）。"""
    return chat_agent._build_agent(use_rag=use_rag, llm=_llm_with_mock(handler))


def _patch_agent(monkeypatch, handler, *, use_rag: bool = True):
    """让 chat() 使用注入 mock LLM 的 agent。"""
    agent = _mock_agent(handler, use_rag=use_rag)
    monkeypatch.setattr(
        chat_agent, "_get_agent", lambda *, use_rag, llm=None: agent
    )
    return agent


# ── 工具集可调用 ──────────────────────────────────────────────────────────


def test_search_spots_tool_invocable():
    """结构化直查工具：search_spots 返回景点 JSON 列表。"""
    out = chat_agent.recommend_agent.search_spots.invoke({"query": "自然"})
    data = json.loads(out)
    assert isinstance(data, list)
    assert all("name" in spot for spot in data)


def test_search_routes_tool_invocable():
    """结构化直查工具：search_routes 返回路线 JSON 列表。"""
    out = chat_agent.recommend_agent.search_routes.invoke({"query": "亲子"})
    data = json.loads(out)
    assert isinstance(data, list)


def test_search_knowledge_tool_invocable():
    """统一检索入口：search_knowledge 返回知识片段列表（空索引时为空列表不报错）。"""
    out = chat_agent.recommend_agent.search_knowledge.invoke(
        {"query": "开放时间", "top_k": 2}
    )
    data = json.loads(out)
    assert isinstance(data, list)


def test_recommend_routes_tool_invocable():
    """推荐工具：recommend_routes 返回推荐响应 JSON（含 summary）。"""
    out = chat_agent.recommend_routes.invoke(
        {"preference": "喜欢自然风光，带老人小孩"}
    )
    data = json.loads(out)
    assert isinstance(data, dict)
    assert "summary" in data


# ── agent 完整工具调用链路 ─────────────────────────────────────────────────


async def test_agent_calls_search_spots_tool():
    """模型先请求 search_spots 工具，执行后模型给出最终回答。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        n = len(calls)
        if n == 1:
            return httpx.Response(
                200, json=_completion([_tool_call_choice("search_spots", {"query": "自然"})])
            )
        return httpx.Response(200, json=_completion([_text_choice("推荐自然风光类景点。")]))

    agent = _mock_agent(handler, use_rag=False)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="有哪些自然风光景点？")]}
    )
    msgs = result["messages"]
    # 最终回答为非工具 AIMessage
    assert msgs[-1].content == "推荐自然风光类景点。"
    # 中间确实执行了 search_spots 工具（ToolMessage）
    tool_msgs = [m for m in msgs if getattr(m, "type", "") == "tool"]
    assert len(tool_msgs) == 1
    tool_data = json.loads(tool_msgs[0].content)
    assert isinstance(tool_data, list)


async def test_agent_calls_search_knowledge_tool():
    """模型请求 search_knowledge 工具时，agent 经统一检索入口查询知识库。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        n = len(calls)
        if n == 1:
            return httpx.Response(
                200,
                json=_completion(
                    [_tool_call_choice("search_knowledge", {"query": "开放时间", "top_k": 2})]
                ),
            )
        return httpx.Response(200, json=_completion([_text_choice("景区 8:00 开放。")]))

    agent = _mock_agent(handler, use_rag=True)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="几点开门？")]}
    )
    msgs = result["messages"]
    assert msgs[-1].content == "景区 8:00 开放。"
    tool_msgs = [m for m in msgs if getattr(m, "type", "") == "tool"]
    assert len(tool_msgs) == 1
    assert json.loads(tool_msgs[0].content) == []


async def test_chat_full_flow_with_rag_sources(monkeypatch):
    """chat() 全链路：调用 search_knowledge 后，响应透出 rag_context/rag_sources。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        n = len(calls)
        if n == 1:
            return httpx.Response(
                200,
                json=_completion(
                    [_tool_call_choice("search_knowledge", {"query": "讲解词", "top_k": 2})]
                ),
            )
        return httpx.Response(200, json=_completion([_text_choice("这是讲解词。")]))

    _patch_agent(monkeypatch, handler, use_rag=True)
    result = await chat_agent.chat("讲个讲解词", user="visitor")
    assert result.answer == "这是讲解词。"
    assert result.conversation_id  # 自动生成新会话 id
    # search_knowledge 返回空列表 → rag_sources 为空但结构兼容
    assert result.rag_sources == []


async def test_chat_auto_generates_conversation_id(monkeypatch):
    """空 conversation_id 自动生成；同 id 复用进程内会话。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("你好！")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    r1 = await chat_agent.chat("你好", conversation_id="")
    r2 = await chat_agent.chat("你好", conversation_id=r1.conversation_id)
    assert r1.conversation_id
    assert r2.conversation_id == r1.conversation_id


# ── 会话 ID 归属判定（双轨回落用） ───────────────────────────────────────


async def test_has_conversation_true_only_for_generated_ids(monkeypatch):
    """has_conversation 仅对本模块生成的 id 返回 True。

    客户端传入的未知 id（如 Dify 生成的会话 id）即使被 LangChain 侧复用
    为会话 key，也不应被判定为本模块所有——回落时须原样透传给 Dify。
    """
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    r1 = await chat_agent.chat("你好", conversation_id="")
    assert chat_agent.has_conversation(r1.conversation_id) is True
    # Dify 生成的 id 被 LangChain 复用建会后仍不算本模块所有
    r2 = await chat_agent.chat("你好", conversation_id="dify-cid")
    assert r2.conversation_id == "dify-cid"
    assert chat_agent.has_conversation("dify-cid") is False
    assert chat_agent.has_conversation("") is False


async def test_has_conversation_survives_lru_eviction(monkeypatch):
    """LRU 淘汰不改变 has_conversation 判定：被淘汰的 LangChain id 对
    Dify 依旧未知，回落时仍需传空串而非原样透传。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    monkeypatch.setattr(chat_agent, "_MAX_CONVERSATIONS", 1)
    ids = []
    for i in range(3):
        r = await chat_agent.chat(f"问{i}", conversation_id="")
        ids.append(r.conversation_id)
    # 仅最新会话留在进程内，早先的已被 LRU 淘汰
    with chat_agent._conversations_lock:
        assert ids[0] not in chat_agent._conversations
    assert chat_agent.has_conversation(ids[0]) is True
    assert chat_agent.has_conversation(ids[2]) is True


async def test_clear_conversation_keeps_generated_id(monkeypatch):
    """clear_conversation 仅清历史：id 仍由本模块生成，归属判定不变。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    r1 = await chat_agent.chat("你好", conversation_id="")
    chat_agent.clear_conversation(r1.conversation_id)
    with chat_agent._conversations_lock:
        assert r1.conversation_id not in chat_agent._conversations
    assert chat_agent.has_conversation(r1.conversation_id) is True


# ── 多轮上下文 ─────────────────────────────────────────────────────────────


async def test_multi_turn_history_passed_to_llm(monkeypatch):
    """第二轮请求体 messages 含第一轮的 user+assistant 历史。"""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        n = len(bodies)
        if n == 1:
            return httpx.Response(200, json=_completion([_text_choice("第一轮回答")]))
        return httpx.Response(200, json=_completion([_text_choice("第二轮回答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    r1 = await chat_agent.chat("第一问", conversation_id="")
    r2 = await chat_agent.chat("第二问", conversation_id=r1.conversation_id)

    assert r2.answer == "第二轮回答"
    # 第二轮请求必须携带历史：user×2 + assistant×1 + 系统提示
    roles = [m["role"] for m in bodies[1]["messages"]]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 1


async def test_history_trimmed_at_limit(monkeypatch):
    """历史超过上限时丢弃最早消息（不无限膨胀）。"""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    cid = await chat_agent.chat("问1", conversation_id="")
    # 灌满 20 条消息（10 轮）+ 额外 3 轮
    for i in range(12):
        await chat_agent.chat(f"问{i}", conversation_id=cid.conversation_id)
    last_roles = [m["role"] for m in bodies[-1]["messages"]]
    assert last_roles.count("user") <= chat_agent._MAX_HISTORY_MESSAGES // 2 + 1


# ── use_rag 开关 ───────────────────────────────────────────────────────────


async def test_use_rag_false_excludes_knowledge_tool(monkeypatch):
    """use_rag=False 时 agent 工具集不含 search_knowledge（结构化工具保留）。"""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("好")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    await chat_agent.chat("你好", use_rag=False)
    tool_names = [t["function"]["name"] for t in bodies[0]["tools"]]
    assert "search_knowledge" not in tool_names
    assert "search_spots" in tool_names
    assert "recommend_routes" in tool_names


# ── 异常与兜底 ─────────────────────────────────────────────────────────────


async def test_llm_config_error_propagates(monkeypatch):
    """LLM 未配置时 chat() 抛 LLMConfigError（由路由层回落 Dify）。"""
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "")
    chat_agent.reset_agent_cache()
    from app.core.llm import LLMConfigError

    with pytest.raises(LLMConfigError):
        await chat_agent.chat("你好")


async def test_chat_recursion_limit_breaks_tool_loop(monkeypatch):
    """异常 LLM 反复请求工具时 agent 步数超限抛 GraphRecursionError
    （回归：langchain 1.3 的 create_agent 默认 recursion_limit=9999，
    不加显式限制会循环执行工具近万次而非快速失败回落 Dify）。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if body.get("stream"):
            raise AssertionError("非流式请求不应发生")
        return httpx.Response(
            200,
            json=_completion(
                [_tool_call_choice("search_spots", {"query": "自然"})]
            ),
        )

    _patch_agent(monkeypatch, handler, use_rag=False)
    from langgraph.errors import GraphRecursionError

    with pytest.raises(GraphRecursionError):
        await chat_agent.chat("你好")
    # 步数上限 25 ≈ 12 轮工具调用，LLM 调用次数远小于 9999
    assert calls < 30
