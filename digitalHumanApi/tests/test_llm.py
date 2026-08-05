"""对话 LLM 工厂（app/core/llm.py）单测。

覆盖：工厂配置读取、缺 key 降级、主/备用端点切换、max_tokens 注入、
DeepSeek reasoning_content 思考格式在 LangChain ChatOpenAI 下的行为
（剥离/空 chunk 不报错，用 httpx.MockTransport 模拟端点响应，不发真实请求）。
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.core.llm import (
    LLMConfigError,
    create_chat_llm,
    get_chat_llm,
    resolve_endpoint,
)

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


# ── 工厂配置读取 ──


def test_create_chat_llm_explicit_endpoint():
    llm = create_chat_llm(
        api_base="https://example.com/v1",
        api_key="sk-abc",
        model="my-model",
        temperature=0.3,
        timeout=30,
    )
    assert llm.model_name == "my-model"
    assert llm.openai_api_base == "https://example.com/v1"
    assert llm.openai_api_key.get_secret_value() == "sk-abc"
    assert llm.temperature == 0.3
    assert llm.request_timeout == 30


def test_get_chat_llm_reads_settings_primary():
    llm = get_chat_llm()
    assert llm.model_name == PRIMARY["LLM_MODEL"]
    assert llm.openai_api_base == PRIMARY["LLM_API_BASE"]
    assert llm.openai_api_key.get_secret_value() == PRIMARY["LLM_API_KEY"]


def test_get_chat_llm_temperature_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TEMPERATURE", 0.2)
    assert get_chat_llm().temperature == 0.2
    # 显式 temperature 覆盖 settings
    assert get_chat_llm(temperature=0.9).temperature == 0.9


def test_create_chat_llm_requires_key():
    with pytest.raises(LLMConfigError):
        create_chat_llm(api_base="https://x/v1", api_key="", model="m")


# ── 缺 key 降级 / 端点切换 ──


def test_fallback_when_primary_key_missing(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    llm = get_chat_llm()
    assert llm.model_name == FALLBACK["LLM_FALLBACK_MODEL"]
    assert llm.openai_api_base == FALLBACK["LLM_FALLBACK_API_BASE"]
    assert llm.openai_api_key.get_secret_value() == FALLBACK["LLM_FALLBACK_API_KEY"]


def test_force_fallback_endpoint():
    llm = get_chat_llm(use_fallback=True)
    assert llm.model_name == FALLBACK["LLM_FALLBACK_MODEL"]
    assert llm.openai_api_base == FALLBACK["LLM_FALLBACK_API_BASE"]


def test_force_fallback_without_fallback_key_keeps_primary(monkeypatch):
    """use_fallback=True 但备用未配置 key → 回退主端点（避免强切导致不可用）。"""
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "")
    llm = get_chat_llm(use_fallback=True)
    assert llm.model_name == PRIMARY["LLM_MODEL"]
    assert llm.openai_api_base == PRIMARY["LLM_API_BASE"]


def test_no_key_at_all_raises(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "")
    with pytest.raises(LLMConfigError, match="均未配置"):
        get_chat_llm()
    with pytest.raises(LLMConfigError):
        resolve_endpoint()


def test_resolve_endpoint_reporting():
    ep = resolve_endpoint()
    assert ep == {
        "api_base": PRIMARY["LLM_API_BASE"],
        "api_key": PRIMARY["LLM_API_KEY"],
        "model": PRIMARY["LLM_MODEL"],
    }
    ep_fb = resolve_endpoint(use_fallback=True)
    assert ep_fb["api_base"] == FALLBACK["LLM_FALLBACK_API_BASE"]


# ── max_tokens 注入（DeepSeek 兼容） ──


def test_max_tokens_injected_via_extra_body():
    """langchain-openai 1.4.1 把 max_tokens 重命名为 max_completion_tokens 发送，
    DeepSeek 系端点不识别 → 工厂必须经 extra_body 注入 max_tokens 原字段。"""
    llm = get_chat_llm(max_tokens=64)
    assert llm.extra_body == {"max_tokens": 64}
    # ChatOpenAI 自带字段保持 None，避免与 extra_body 的 max_tokens 同时出现
    assert llm.max_tokens is None


def test_extra_body_custom_fields_passthrough():
    llm = get_chat_llm(max_tokens=32, extra_body={"thinking": "high"})
    assert llm.extra_body == {"max_tokens": 32, "thinking": "high"}


def test_no_max_tokens_no_extra_body():
    assert get_chat_llm().extra_body is None


# ── DeepSeek 思考格式兼容性（MockTransport，不发真实请求） ──

_DEEPSEEK_CHAT_COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "deepseek-v4-flash",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {
                "role": "assistant",
                "content": "最终回答",
                "reasoning_content": "思考过程（不应泄漏到 LangChain 层）",
            },
        }
    ],
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "completion_tokens_details": {"reasoning_tokens": 12},
    },
}


def _llm_with_mock(handler, use_fallback: bool = False):
    """构造注入 MockTransport 的 ChatOpenAI（经工厂，验证完整链路）。"""
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        use_fallback=use_fallback,
        http_client=httpx.Client(transport=transport),
    )


def test_reasoning_content_stripped_from_response():
    """非流式响应：reasoning_content 被自动剥离，content 为最终回答。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_DEEPSEEK_CHAT_COMPLETION)

    llm = _llm_with_mock(handler)
    resp = llm.invoke("你好")
    assert resp.content == "最终回答"
    # 思考内容不泄漏到 additional_kwargs
    assert "reasoning" not in json.dumps(resp.additional_kwargs, default=str).lower()
    # 请求走主端点
    assert seen["url"].startswith(PRIMARY["LLM_API_BASE"])
    assert seen["body"]["model"] == PRIMARY["LLM_MODEL"]


def test_stream_thinking_chunks_empty_and_no_error():
    """流式：思考阶段 chunk.content 为空串（自动丢弃），不报错，最终可合并。"""
    chunks = [
        {"delta": {"role": "assistant", "content": "", "reasoning_content": "思考一"}, "finish_reason": None},
        {"delta": {"content": "", "reasoning_content": "思考二"}, "finish_reason": None},
        {"delta": {"content": "你好"}, "finish_reason": None},
        {"delta": {"content": "世界"}, "finish_reason": "stop"},
    ]

    def sse_line(chunk: dict) -> str:
        body = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": "deepseek-v4-flash",
            "choices": [{"index": 0, **chunk}],
        }
        return f"data: {json.dumps(body)}\n\n"

    sse = "".join(sse_line(c) for c in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=sse.encode(),
        )

    llm = _llm_with_mock(handler)
    collected = [c for c in llm.stream("你好")]
    assert "".join(c.content for c in collected) == "你好世界"


def test_empty_content_response_no_error():
    """思考耗尽（max_tokens 过小）时 content 为空：不报错，返回空串。"""
    empty = dict(_DEEPSEEK_CHAT_COMPLETION)
    empty["choices"] = [
        {
            "index": 0,
            "finish_reason": "length",
            "message": {"role": "assistant", "content": "", "reasoning_content": "思考耗尽"},
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=empty)

    resp = _llm_with_mock(handler).invoke("复杂问题")
    assert resp.content == ""


def test_request_payload_uses_max_tokens_field():
    """请求体必须含 max_tokens（DeepSeek 系端点），不得含 max_completion_tokens。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_DEEPSEEK_CHAT_COMPLETION)

    llm = _llm_with_mock(handler, use_fallback=True)
    # 工厂注入 max_tokens=32 → 请求体出现原生 max_tokens 字段
    llm = get_chat_llm(use_fallback=True, max_tokens=32, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    llm.invoke("你好")
    assert seen["body"]["max_tokens"] == 32
    assert "max_completion_tokens" not in seen["body"]
    # 注意：invoke 时显式传 max_tokens 会被 langchain 重命名为
    # max_completion_tokens（OpenAI 新命名）——DeepSeek 系端点请勿这样传
