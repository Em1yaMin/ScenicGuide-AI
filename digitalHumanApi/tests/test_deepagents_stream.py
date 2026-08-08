"""deepagents 装配层（A2 试点 → A3 主链路）——流式 SSE 契约（A1 报告 §5 映射）单测。

覆盖：
1. 事件序列：step（工具调用）→ token（回答增量）→ done
2. **suppress_tokens 语义**（A1 实测必现）：工具调用消息的内联 content 被
   抑制（模型在调用工具前会输出正文 token），ToolMessage 后恢复输出
3. step 按调用 id 去重（同一工具调用拆分多个 chunk 只产出一个 step）
4. 记忆工具步骤文案（_STEP_LABELS 合并）
5. done 事件契约（conversation_id 透出/rag_context/rag_sources/intent）+
   user 传入图状态 + recursion_limit 显式收紧（A3 起会话管理接入：
   done 携带生成的 conversation_id）
6. 意图分流在流式入口同样生效（复杂 query 注入规划指令）

不依赖真实 LLM：注入假 agent（记录输入并产出构造好的消息 chunk 流）。
"""

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from app.services import chat_agent
from app.services.deepagents import agent as dg_agent
from app.services.deepagents.agent import _MAX_AGENT_STEPS, _PLAN_INSTRUCTION


@pytest.fixture(autouse=True)
def _pin_session_and_memory(monkeypatch):
    """隔离会话存储 + 记忆提取钩子 no-op（A3：chat_stream() 接入会话管理与钩子）。"""
    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


class _FakeStreamAgent:
    """假 agent：记录 astream 输入/config，产出预设 chunk 流（消息模式）。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.inputs = None
        self.config = None

    async def astream(self, inputs, stream_mode="messages", config=None):
        self.inputs = inputs
        self.config = config
        assert stream_mode == "messages"
        for chunk in self._chunks:
            yield chunk, {}


def _tool_chunk(name: str, call_id: str, content: str = "") -> AIMessageChunk:
    """工具调用增量块（AIMessageChunk 带 tool_call_chunks）。"""
    return AIMessageChunk(
        content=content,
        tool_call_chunks=[
            {
                "name": name,
                "id": call_id,
                "args": "",
                "index": 0,
            }
        ],
    )


def _text_chunk(text: str) -> AIMessageChunk:
    return AIMessageChunk(content=text)


def _tool_result(name: str, call_id: str, content: str = "{}") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name=name)


# ── 事件序列与 suppress_tokens ────────────────────────────────────────────


async def test_stream_step_token_done_sequence(monkeypatch):
    """事件序列：step（工具调用）→ token（回答增量）→ done。"""
    fake = _FakeStreamAgent(
        [
            _tool_chunk("search_spots", "call_1"),
            _text_chunk("推荐"),  # 工具调用消息内的 content → 必须抑制
            _tool_result("search_spots", "call_1"),
            _text_chunk("自然风光"),
            _text_chunk("景点。"),
        ]
    )
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake)
    events = [e async for e in dg_agent.chat_stream("有哪些自然景点？", user="u1")]

    types = [e["type"] for e in events]
    assert types == ["step", "token", "token", "done"]
    # suppress_tokens：工具调用消息的内联 content（"推荐"）不输出
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert answer == "自然风光景点。"
    assert "推荐" not in answer
    # step 事件：工具名 + 中文步骤文案
    assert events[0]["tool"] == "search_spots"
    assert events[0]["label"] == "正在查询景点…"
    # done 事件契约（A3：conversation_id 为会话存储生成的 id，非空）
    done = events[-1]
    assert done["conversation_id"]
    assert done["rag_sources"] == []
    assert done["rag_context"] == ""
    assert done["intent"] == "simple"


async def test_stream_step_dedup_by_call_id(monkeypatch):
    """同一工具调用的多个 chunk（call id 相同）只产出一个 step 事件。"""
    fake = _FakeStreamAgent(
        [
            _tool_chunk("recall_user_memory", "call_9"),
            _tool_chunk("recall_user_memory", "call_9"),  # 重复 chunk
            _tool_result("recall_user_memory", "call_9", '[{"content": "喜欢拍照"}]'),
            _text_chunk("记得您喜欢拍照。"),
        ]
    )
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake)
    events = [e async for e in dg_agent.chat_stream("我有什么偏好？", user="u1")]
    steps = [e for e in events if e["type"] == "step"]
    assert len(steps) == 1
    # 记忆工具步骤文案（_STEP_LABELS 合并生效）
    assert steps[0]["tool"] == "recall_user_memory"
    assert steps[0]["label"] == "正在回忆您的偏好…"


async def test_stream_suppress_resets_after_tool_message(monkeypatch):
    """ToolMessage 后 suppress_tokens 复位：后续文本正常输出。"""
    fake = _FakeStreamAgent(
        [
            _tool_chunk("search_routes", "c1"),
            _text_chunk("抑制这段"),
            _tool_result("search_routes", "c1"),
            _text_chunk("路线"),
            _tool_chunk("get_route_detail", "c2"),  # 第二轮工具调用再次抑制
            _text_chunk("再抑制"),
            _tool_result("get_route_detail", "c2"),
            _text_chunk("推荐完成。"),
        ]
    )
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake)
    events = [e async for e in dg_agent.chat_stream("推荐一条路线", user="u1")]
    steps = [e for e in events if e["type"] == "step"]
    assert [s["tool"] for s in steps] == ["search_routes", "get_route_detail"]
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert answer == "路线推荐完成。"
    assert "抑制" not in answer and "再抑制" not in answer


async def test_stream_user_and_recursion_limit_passed(monkeypatch):
    """流式入口：user 传入图状态、recursion_limit 显式收紧（30）。"""
    fake = _FakeStreamAgent([_text_chunk("你好"), _text_chunk("！")])
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake)
    events = [e async for e in dg_agent.chat_stream("你好", user="user_42")]
    assert fake.inputs["user"] == "user_42"
    assert fake.config["recursion_limit"] == _MAX_AGENT_STEPS
    assert "".join(e["text"] for e in events if e["type"] == "token") == "你好！"


async def test_stream_complex_intent_injects_plan_instruction(monkeypatch):
    """流式入口意图分流：复杂 query 的 messages 含规划指令（intent 透出）。"""
    fake = _FakeStreamAgent([_text_chunk("行程规划如下")])
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake)
    events = [e async for e in dg_agent.chat_stream("帮我规划灵山一日游", user="u1")]
    done = events[-1]
    assert done["intent"] == "complex"
    msgs = fake.inputs["messages"]
    assert msgs[0].type == "system"
    assert _PLAN_INSTRUCTION in msgs[0].content
    assert msgs[-1].content == "帮我规划灵山一日游"
    # 简单 query 不注入
    fake2 = _FakeStreamAgent([_text_chunk("好")])
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake2)
    events2 = [e async for e in dg_agent.chat_stream("你好", user="u1")]
    assert events2[-1]["intent"] == "simple"
    assert all(m.type != "system" for m in fake2.inputs["messages"])


async def test_stream_rag_sources_from_knowledge_tool(monkeypatch):
    """search_knowledge 的 ToolMessage 内容 → done 事件 rag_sources 提取。"""
    rag_json = (
        '[{"content": "灵山胜境开放时间", "source": "doc/景区知识库/a.md",'
        ' "score": 0.8, "namespace": "documents"}]'
    )
    fake = _FakeStreamAgent(
        [
            _tool_chunk("search_knowledge", "c1"),
            _tool_result("search_knowledge", "c1", rag_json),
            _text_chunk("灵山 8 点开放。"),
        ]
    )
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake)
    events = [e async for e in dg_agent.chat_stream("几点开门？", user="u1")]
    done = events[-1]
    assert done["type"] == "done"
    assert done["rag_sources"] == [
        {
            "source": "doc/景区知识库/a.md",
            "score": 0.8,
            "namespace": "documents",
        }
    ]
    assert "灵山胜境开放时间" in done["rag_context"]
