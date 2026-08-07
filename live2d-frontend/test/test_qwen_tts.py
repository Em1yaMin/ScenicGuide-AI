# -*- coding: utf-8 -*-
'''
QwenAPI TTS 引擎单测（mock dashscope SpeechSynthesizer，不发真实请求）。

覆盖：
1. 正常合成：mock synthesizer 返回 mp3 bytes → mp3ToWav 转 wav → AudioMessage 契约
   （format=WAV / sampleRate / sampleWidth / desc），模型/音色/URL/鉴权头透传正确
2. 失败降级：synthesizer 抛异常 → run 返回 None（不抛错）
3. dashscope SDK 未安装 → 返回 None 不抛错（懒导入降级）
4. 未配置 API Key → 返回 None
5. 引擎注册名 QwenAPI、checkKeys 要求 API_KEY
'''
import asyncio
import base64
import os

import pytest

from yacs.config import CfgNode as CN

from digitalHuman.utils import TextMessage, AudioFormatType
from digitalHuman.engine.tts.qwenTTS import QwenAPI, DEFAULT_MODEL, DEFAULT_VOICE, DEFAULT_WS_URL

# 合法最小 mp3（440Hz 0.1s 单声道 24kHz，ffmpeg 生成，base64 内嵌避免仓库提交二进制）
MINI_MP3 = base64.b64decode(
    """SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjYwLjE2LjEwMAAAAAAAAAAAAAAA//OEwAAAAAAAAAAAAEluZm8AAAAPAAAABwAAA2AAVVVVVVVVVVVVVVVVVVVxcXFxcXFxcXFxcXFxcY6Ojo6Ojo6Ojo6Ojo6Oqqqqqqqqqqqqqqqqqqqqx8fHx8fHx8fHx8fHx8fj4+Pj4+Pj4+Pj4+Pj4///////////////////AAAAAExhdmM2MC4zMQAAAAAAAAAAAAAAACQEIAAAAAAAAANgCaMl8wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA//NExAASUG54H1gYAFZJbgF3rvWHVOmOoO09uhbA1tNrTWkzlAR0Aa63fjcYlkojEspKSxgD4Pg+D9QIOy5/gg6c6fOcv5zp5cEHSgDD+TBDl/d/hjpVMAhQwuHqsc1B//NExAkUSTJsAZyAAGY7ax7I/AxBnyxgYVQB8JAmcSAYbCiWNGmzOBZKDbULCfxSQlIQsQH/GZGVJoixAv/MSdIqZF4vf/mJdLqQNN/KhIGhKEv+DSo6hKWhwn6h8EAD//NExAoUAFYsVd4AAJa0BAGmB6FkYVA+RgnBomEqIkeTr3BnvCHmFKFQYKoJxgtgTGAAAeWRL0q3ONIglJbTHFbPQ3/u10eq/q/d+rT6Pqv6f+/OGGtqAAkATAwGjD0g//NExA0REFYcAO/0QE3pvkyOAUwIMHxNH4XIzClQQc/Ew05sxQYs2ioqdr8OW3fs/ZkP9Cn/eUc7/9dv+bsRoo/71fqqTHYPbXwWwMAoC4wSAFDCSCQMFYLYxSxFTQvh//NExBsYyN5AAV4YAIDFWGuMSIIgwQAKAYAiYDYBpgDgCqTbuy+H6enjcbjdOACCdxbu4AIJ3f3ABBVz9wAMCeXBBb4kOawQ4kOcEOUdwxyjuGOryFWSaAWLRJGa08P+//NExAoU6T54KZuAAGALxul+mVDX+PcgGN6OU/4VgRAWzIaaHeLeJ8E7kTMWWj44BmyfIgRetdfy+ThPmZcL69f+mXC+tAzX/BMWC4q3/ULiosLsb/9STEFNRTMuMTAw//NExAkAAANIAcAAAKqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"""
)


def _make_cfg(**overrides):
    cfg = {
        "NAME": "QwenAPI",
        "VERSION": "v0.0.1",
        "API_KEY": "test-dashscope-key",
        "MODEL": DEFAULT_MODEL,
        "VOICE": DEFAULT_VOICE,
        "BASE_WEBSOCKET_API_URL": DEFAULT_WS_URL,
        "SAMPLE_RATE": 24000,
    }
    cfg.update(overrides)
    return CN(cfg, new_allowed=True)


def _fake_synthesizer_factory(monkeypatch, captured: dict, result: bytes = MINI_MP3, error: Exception = None):
    """构造 FakeSpeechSynthesizer 并注入 qwenTTS 的懒导入位置。

    通过 monkeypatch 把 dashscope 包（父模块 + audio.tts_v2 子模块）替换为本地桩
    （不依赖真实 SDK 安装）：引擎内先 `import dashscope` 设置 api_key，再
    from-import 子模块，两者都需短路到假模块。
    """
    class FakeAudioFormat:
        MP3_24000HZ_MONO_256KBPS = "mp3-24000-mono-256kbps"

    class FakeSpeechSynthesizer:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs
            self._kwargs = kwargs

        def call(self, text):
            captured["call_text"] = text
            if error is not None:
                raise error
            return result

    import types
    import sys

    fake_parent = types.ModuleType("dashscope")
    fake_parent.api_key = None
    fake_module = types.ModuleType("dashscope.audio.tts_v2")
    fake_module.SpeechSynthesizer = FakeSpeechSynthesizer
    fake_module.AudioFormat = FakeAudioFormat

    monkeypatch.setitem(sys.modules, "dashscope", fake_parent)
    monkeypatch.setitem(sys.modules, "dashscope.audio.tts_v2", fake_module)
    # 引擎内 `from dashscope.audio.tts_v2 import ...` 走已注入的模块
    return fake_module


@pytest.fixture
def clean_env(monkeypatch):
    """隔离环境变量 QWEN_API_KEY（测试互不污染）。"""
    monkeypatch.delenv("QWEN_API_KEY", raising=False)


@pytest.mark.asyncio
async def test_qwen_api_success(monkeypatch, clean_env):
    """正常合成：mp3 → wav 转换 + AudioMessage 契约 + 参数透传。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, result=MINI_MP3)

    engine = QwenAPI(_make_cfg(API_KEY="env-or-cfg-key"))
    msg = await engine.run(TextMessage(data="你好，世界"))

    assert msg is not None
    assert msg.format == AudioFormatType.WAV
    assert msg.sampleRate == 24000
    assert msg.sampleWidth == 2
    assert msg.desc == "你好，世界"
    assert len(msg.data) > 0  # 转出的 wav 非空

    # 参数透传：模型/音色/WebSocket URL/鉴权头
    kwargs = captured["init_kwargs"]
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["voice"] == DEFAULT_VOICE
    assert kwargs["url"] == DEFAULT_WS_URL
    assert kwargs["format"] == "mp3-24000-mono-256kbps"
    assert kwargs["headers"] == {"Authorization": "Bearer env-or-cfg-key"}
    # 文本透传
    assert captured["call_text"] == "你好，世界"


@pytest.mark.asyncio
async def test_qwen_api_cfg_overrides(monkeypatch, clean_env):
    """cfg 自定义模型/音色/URL 优先于默认值。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, result=MINI_MP3)
    engine = QwenAPI(_make_cfg(
        MODEL="custom-model",
        VOICE="custom-voice",
        BASE_WEBSOCKET_API_URL="wss://custom.example.com/v1",
    ))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is not None
    kwargs = captured["init_kwargs"]
    assert kwargs["model"] == "custom-model"
    assert kwargs["voice"] == "custom-voice"
    assert kwargs["url"] == "wss://custom.example.com/v1"


@pytest.mark.asyncio
async def test_qwen_api_env_key_priority(monkeypatch):
    """环境变量 QWEN_API_KEY 优先于配置文件占位（key 不入库的部署方式）。"""
    captured = {}
    monkeypatch.setenv("QWEN_API_KEY", "env-real-key")
    _fake_synthesizer_factory(monkeypatch, captured, result=MINI_MP3)
    engine = QwenAPI(_make_cfg(API_KEY="cfg-placeholder"))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is not None
    assert captured["init_kwargs"]["headers"] == {"Authorization": "Bearer env-real-key"}


@pytest.mark.asyncio
async def test_qwen_api_synthesizer_error_returns_none(monkeypatch, clean_env):
    """synthesizer 调用异常 → 返回 None（与现有引擎降级行为一致，不抛错）。"""
    captured = {}
    _fake_synthesizer_factory(
        monkeypatch, captured,
        error=RuntimeError("connection failed / invalid api key"),
    )
    engine = QwenAPI(_make_cfg())
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None


@pytest.mark.asyncio
async def test_qwen_api_missing_sdk_returns_none(monkeypatch, clean_env):
    """dashscope 未安装（懒导入 ImportError）→ 返回 None 不抛错。"""
    import sys

    # 移除注入的模块并确保 import 失败
    sys.modules.pop("dashscope.audio.tts_v2", None)

    def raise_importerror(*a, **k):
        raise ImportError("No module named 'dashscope'")

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "dashscope.audio.tts_v2":
            raise ImportError("No module named 'dashscope'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    engine = QwenAPI(_make_cfg())
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None


@pytest.mark.asyncio
async def test_qwen_api_missing_key_returns_none(monkeypatch, clean_env):
    """未配置 API Key（环境变量与 cfg 均为空）→ 返回 None。"""
    engine = QwenAPI(_make_cfg(API_KEY=""))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None


@pytest.mark.asyncio
async def test_qwen_api_empty_text_returns_none(monkeypatch, clean_env):
    """输入文本为空 → 返回 None。"""
    engine = QwenAPI(_make_cfg())
    msg = await engine.run(TextMessage(data=""))
    assert msg is None


def test_qwen_api_registered_and_checks_keys():
    """引擎注册名 QwenAPI、checkKeys 要求 API_KEY。"""
    from digitalHuman.engine.builder import TTSEngines
    assert "QwenAPI" in TTSEngines.list()
    engine = QwenAPI(_make_cfg())
    assert engine.checkKeys() == ["API_KEY"]
    # 缺 API_KEY 的 cfg 在 BaseEngine.__init__ 即报 KeyError
    with pytest.raises(KeyError):
        QwenAPI(CN({"NAME": "QwenAPI"}, new_allowed=True))
