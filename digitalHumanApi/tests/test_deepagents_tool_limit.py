"""ToolCallLimitMiddleware 装配与行为（A4 成本/速度调优）。

覆盖：
1. `_build_agent` 装配检查：middleware 含 ToolCallLimitMiddleware，参数取
   配置（run_limit=AGENT_MAX_TOOL_CALLS、exit_behavior="continue"）——
   用 monkeypatch 拦截 create_deep_agent 捕获 kwargs，避免 24s 真实构建
2. 行为验证：langchain create_agent + 注入 MockTransport LLM（一直请求
   工具）——run_limit 内正常执行，超限后工具调用被拦截（错误 ToolMessage
   回给模型，优雅降级不报错、不抛 ToolCallLimitExceededError），模型基于
   已有信息产出最终回答
3. 状态语义：run 级计数——每次 ainvoke 独立计数（stateless 图无跨轮累积）

不依赖真实 LLM：httpx.MockTransport 模拟 DeepSeek 兼容 tool-calling 响应。
"""

import json

import httpx
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services.deepagents import agent as dg_agent


@pytest.fixture(autouse=True)
def _pin_llm_config(monkeypatch):
    """固定 LLM 端点配置（假 key），避免依赖真实环境变量。"""
    monkeypatch.setattr(settings, "LLM_API_BASE", "https://opencode.ai/zen/go/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-tool-limit-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_BASE", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "sk-fallback-test")
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODEL", "deepseek-chat")
    yield


def _completion(choices):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "deepseek-v4-flash",
        "choices": choices,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _tool_choice(name: str, args: dict, call_id: str = "call_1"):
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
    """注入 MockTransport 的 ChatOpenAI（async/sync client 双注入，见
    test_chat_agent 同款模式）。"""
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


# ── 装配检查 ──────────────────────────────────────────────────────────────


async def test_build_agent_wires_tool_call_limit_middleware(monkeypatch):
    """_build_agent 装配 ToolCallLimitMiddleware：run_limit 取配置、优雅降级。"""
    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(dg_agent, "create_deep_agent", fake_create_deep_agent)
    llm = object()
    dg_agent._build_agent(use_rag=True, llm=llm)
    middleware = captured["middleware"]
    assert len(middleware) == 1
    limiter = middleware[0]
    assert isinstance(limiter, ToolCallLimitMiddleware)
    assert limiter.run_limit == settings.AGENT_MAX_TOOL_CALLS
    assert limiter.exit_behavior == "continue"
    # 其余装配参数不受影响
    assert captured["name"] == "scenic_deep_agent"
    assert captured["state_schema"] is dg_agent.ScenicDeepAgentState


async def test_default_run_limit_is_15(monkeypatch):
    """默认上限 15（A1 实测：复杂任务平均 9 步、峰值 13 步，留余量）。"""
    # settings 实例在导入时以环境变量构建；若环境未设置则取默认 15。
    # 若外部环境（CI/本地 .env）显式设置了该变量，按环境值断言（配置优先）
    import os

    expected = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "15"))
    assert settings.AGENT_MAX_TOOL_CALLS == expected
    if "AGENT_MAX_TOOL_CALLS" not in os.environ:
        assert expected == 15


# ── 行为验证（真实 create_agent + MockTransport LLM） ────────────────────


def _build_limited_agent(handler, run_limit=3):
    """构造带 ToolCallLimitMiddleware 的最小 agent（单工具）。"""
    calls = []

    @tool
    def probe(q: str) -> str:
        """probe tool."""
        calls.append(q)
        return f"probed:{q}"

    llm = _llm_with_mock(handler)
    agent = create_agent(
        model=llm,
        tools=[probe],
        system_prompt="你是测试助手",
        middleware=[
            ToolCallLimitMiddleware(run_limit=run_limit, exit_behavior="continue")
        ],
        name="limit_test_agent",
    )
    return agent, calls


async def test_tool_call_limit_blocks_excess_and_answers_gracefully():
    """超限后继续拦截（错误 ToolMessage 回给模型），模型基于已有信息作答，
    不抛异常（exit_behavior=continue 优雅降级）。"""
    # 模型序列：5 次请求工具（第 4、5 次将被拦截）+ 1 次直接作答
    responses = [
        _completion([_tool_choice("probe", {"q": f"q{i}"}, call_id=f"call_{i}")])
        for i in range(5)
    ]
    responses.append(_completion([_text_choice("最终回答")]))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    agent, calls = _build_limited_agent(handler, run_limit=3)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="测试")]},
        config={"recursion_limit": 20},
    )
    # 工具只执行了 3 次（第 4/5 次被中间件拦截）
    assert calls == ["q0", "q1", "q2"]
    # 模型最终产出回答（中间件注入的错误 ToolMessage 让模型转向直接作答）
    final = [
        m for m in result["messages"]
        if getattr(m, "type", "") == "ai" and not getattr(m, "tool_calls", None)
    ]
    assert final and final[-1].content == "最终回答"
    # 被拦截的工具调用收到错误 ToolMessage（status=error）
    blocked = [
        m for m in result["messages"]
        if getattr(m, "type", "") == "tool" and getattr(m, "status", "") == "error"
    ]
    assert len(blocked) == 2
    assert "Tool call limit exceeded" in blocked[0].content


async def test_tool_call_limit_within_limit_unchanged():
    """未超限时行为与无中间件一致（工具全部执行、正常作答）。"""
    responses = [
        _completion([_tool_choice("probe", {"q": "q0"}, call_id="call_0")]),
        _completion([_text_choice("正常回答")]),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    agent, calls = _build_limited_agent(handler, run_limit=3)
    await agent.ainvoke(
        {"messages": [HumanMessage(content="测试")]},
        config={"recursion_limit": 20},
    )
    assert calls == ["q0"]


async def test_run_limit_is_per_invocation():
    """run 级计数：每次 ainvoke 独立（stateless 图无跨轮累积）——
    两轮各可执行 run_limit 次工具。

    每轮模型序列：3 次执行 + 2 次被拦截（超限）+ 1 次作答 = 6 个响应。
    """
    responses = []
    for round_no in range(2):
        base = round_no * 6
        responses.extend(
            _completion([_tool_choice("probe", {"q": f"r{i}"}, call_id=f"c{i}")])
            for i in range(base, base + 5)
        )
        responses.append(_completion([_text_choice(f"A{round_no}")]))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses.pop(0))

    agent, calls = _build_limited_agent(handler, run_limit=3)
    for _ in range(2):
        await agent.ainvoke(
            {"messages": [HumanMessage(content="测试")]},
            config={"recursion_limit": 20},
        )
    # 两轮各执行 3 次（无跨轮累积限制）
    assert calls == ["r0", "r1", "r2", "r6", "r7", "r8"]
