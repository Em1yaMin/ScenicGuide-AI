# -*- coding: utf-8 -*-
'''
QwenAPI TTS 引擎单测（mock dashscope SpeechSynthesizer，不发真实请求）。

覆盖：
1. 正常合成：mock synthesizer 回调流式（on_data 分帧 → on_complete）累积完整
   mp3 → mp3ToWav 转 wav → AudioMessage 契约（format=WAV / sampleRate /
   sampleWidth / desc），模型/音色/URL/鉴权头/超时透传正确
2. 失败降级（「说话说一半」修复路径，2026-08-11）：
   - task-failed（on_error）：部分音频丢弃 → 重试 → 均失败返回 None
   - WebSocket 停滞（首帧后无音频帧）：停滞判定快速失败 → 重试
   - 完全无事件（半开连接）：总超时兜底 → 重试
   - 空音频 / synthesizer 抛异常：返回 None（不抛错，与现有引擎降级行为一致）
   - 重试成功：首次失败 → 二次成功返回完整音频（掩盖偶发网络抖动）
   - finally 兜底 close 连接
3. dashscope SDK 未安装 → 返回 None 不抛错（懒导入降级）
4. 未配置 API Key → 返回 None
5. 引擎注册名 QwenAPI、checkKeys 要求 API_KEY
'''
import base64

import pytest

from yacs.config import CfgNode as CN

from digitalHuman.utils import TextMessage, AudioFormatType
from digitalHuman.engine.tts.qwenTTS import (
    QwenAPI,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    DEFAULT_WS_URL,
    DEFAULT_TIMEOUT_MS,
    DEFAULT_STALL_TIMEOUT,
    DEFAULT_RETRIES,
)

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
        "TIMEOUT_MS": DEFAULT_TIMEOUT_MS,
        "STALL_TIMEOUT": DEFAULT_STALL_TIMEOUT,
        "RETRIES": DEFAULT_RETRIES,
    }
    cfg.update(overrides)
    return CN(cfg, new_allowed=True)


def _fake_synthesizer_factory(monkeypatch, captured, result=MINI_MP3, mode="success", fail_first=0):
    """构造 FakeSpeechSynthesizer 并注入 qwenTTS 的懒导入位置。

    通过 monkeypatch 把 dashscope 包（父模块 + audio.tts_v2 子模块）替换为本地桩
    （不依赖真实 SDK 安装）：引擎内先 `import dashscope` 设置 api_key，再
    from-import 子模块，两者都需短路到假模块。假 synthesizer 模拟 SDK 回调
    流式协议（与 dashscope 1.26.x 实测行为一致）：

    mode:
    - "success"（默认）：on_data 分帧 → on_complete，返回完整音频
    - "failed"：on_data(部分帧) → on_error（task-failed，部分音频必须丢弃）
    - "stall_after_frame"：on_data(首帧) 后无任何事件（半开连接）→ 停滞判定
    - "silent"：完全无事件 → 总超时兜底
    - "empty"：on_complete 但无音频 → 空音频报错
    - "error"：call() 直接抛异常（连接/鉴权失败）

    fail_first：前 N 次 call() 按 "error" 处理，之后按 mode 处理（重试成功路径）。
    """
    class FakeAudioFormat:
        MP3_24000HZ_MONO_256KBPS = "mp3-24000-mono-256kbps"

    class FakeResultCallback:
        """与 SDK ResultCallback 同协议（含 no-op 默认方法）。"""

        def on_open(self):
            pass

        def on_complete(self):
            pass

        def on_error(self, message):
            pass

        def on_close(self):
            pass

        def on_event(self, message):
            pass

        def on_data(self, data):
            pass

    class FakeSpeechSynthesizer:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs
            self._kwargs = kwargs
            # 失败计数跨实例共享：每次重试新建 synthesizer，但前 N 次 call() 仍需
            # 按 fail_first 失败（重试成功路径依赖）
            captured.setdefault("_calls", 0)

        def call(self, text, timeout_millis=None):
            captured["call_text"] = text
            captured["timeout_millis"] = timeout_millis
            captured["_calls"] += 1
            callback = self._kwargs.get("callback")
            if captured["_calls"] <= fail_first or mode == "error":
                # 连接/鉴权失败：call() 直接抛异常（SDK __connect/__start_stream 路径）
                raise RuntimeError("simulated ws connection failure")
            if mode == "failed":
                if callback:
                    callback.on_data(b"partial-mp3-frame")
                    callback.on_error("simulated task-failed: model error")
                return None
            if mode == "stall_after_frame":
                if callback:
                    callback.on_data(result[: len(result) // 2])
                return None  # 首帧后无任何事件 → wait_complete 停滞判定
            if mode == "silent":
                return None  # 完全无事件 → wait_complete 总超时判定
            if mode == "empty":
                if callback:
                    callback.on_complete()
                return None
            # 默认 success：分帧模拟流式到达（帧顺序无关，累积即完整）
            if callback:
                callback.on_data(result[: len(result) // 2])
                callback.on_data(result[len(result) // 2:])
                callback.on_complete()
            return None

        def close(self):
            captured["closed"] = captured.get("closed", 0) + 1

    import types
    import sys

    fake_parent = types.ModuleType("dashscope")
    fake_parent.api_key = None
    fake_module = types.ModuleType("dashscope.audio.tts_v2")
    fake_module.SpeechSynthesizer = FakeSpeechSynthesizer
    fake_module.AudioFormat = FakeAudioFormat
    fake_module.ResultCallback = FakeResultCallback

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
    """正常合成：回调流式累积 mp3 → wav 转换 + AudioMessage 契约 + 参数透传。"""
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

    # 参数透传：模型/音色/WebSocket URL/鉴权头/超时
    kwargs = captured["init_kwargs"]
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["voice"] == DEFAULT_VOICE
    assert kwargs["url"] == DEFAULT_WS_URL
    assert kwargs["format"] == "mp3-24000-mono-256kbps"
    assert kwargs["headers"] == {"Authorization": "Bearer env-or-cfg-key"}
    assert kwargs["callback"] is not None  # 回调流式路径
    # 文本透传
    assert captured["call_text"] == "你好，世界"
    # 有界超时：WebSocket 偶发中断不再无限阻塞
    assert captured["timeout_millis"] == DEFAULT_TIMEOUT_MS
    # finally 兜底关闭连接
    assert captured.get("closed", 0) >= 1


@pytest.mark.asyncio
async def test_qwen_api_cfg_overrides(monkeypatch, clean_env):
    """cfg 自定义模型/音色/URL/超时 优先于默认值。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, result=MINI_MP3)
    engine = QwenAPI(_make_cfg(
        MODEL="custom-model",
        VOICE="custom-voice",
        BASE_WEBSOCKET_API_URL="wss://custom.example.com/v1",
        TIMEOUT_MS=45000,
    ))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is not None
    kwargs = captured["init_kwargs"]
    assert kwargs["model"] == "custom-model"
    assert kwargs["voice"] == "custom-voice"
    assert kwargs["url"] == "wss://custom.example.com/v1"
    assert captured["timeout_millis"] == 45000


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
async def test_qwen_api_connection_error_retry_then_none(monkeypatch, clean_env):
    """call() 抛异常（连接失败）→ 重试 RETRIES 次 → 均失败返回 None（不抛错）。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, mode="error")
    engine = QwenAPI(_make_cfg(RETRIES=2))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None
    # 共尝试 RETRIES+1 次
    assert captured["init_kwargs"]  # synthesizer 每次重建（新建连接）
    assert captured["closed"] >= 3  # 每次尝试 finally 都 close


@pytest.mark.asyncio
async def test_qwen_api_retry_success_after_first_failure(monkeypatch, clean_env):
    """首次失败 → 重试成功返回完整音频（掩盖偶发网络抖动）。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, result=MINI_MP3, fail_first=1)
    engine = QwenAPI(_make_cfg())
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is not None
    assert len(msg.data) > 0
    assert captured["closed"] >= 2  # 两次尝试都 close


@pytest.mark.asyncio
async def test_qwen_api_task_failed_returns_none(monkeypatch, clean_env):
    """task-failed（on_error，部分音频）→ 丢弃部分音频 → 重试 → 均失败返回 None。

    修复「说一半」核心：服务端合成中途失败时 SDK 会把已收到的部分音频交回，
    必须整体丢弃（否则 mp3 截断转 wav 即「说话说一半就终止」）。
    """
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, mode="failed")
    engine = QwenAPI(_make_cfg())
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None
    assert captured["closed"] >= 2  # 重试路径（RETRIES=1 → 2 次尝试）


@pytest.mark.asyncio
async def test_qwen_api_stall_aborts_and_retries(monkeypatch, clean_env):
    """首帧后音频停滞（半开连接）→ 停滞判定快速失败 → 重试成功。"""
    captured = {}
    # 第一次停滞，第二次成功
    state = {"first": True}

    class FakeAudioFormat:
        MP3_24000HZ_MONO_256KBPS = "mp3-24000-mono-256kbps"

    class FakeResultCallback:
        def on_open(self):
            pass

        def on_complete(self):
            pass

        def on_error(self, message):
            pass

        def on_close(self):
            pass

        def on_event(self, message):
            pass

        def on_data(self, data):
            pass

    class FakeSpeechSynthesizer:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs
            self._kwargs = kwargs
            self._calls = 0

        def call(self, text, timeout_millis=None):
            captured["call_text"] = text
            captured["timeout_millis"] = timeout_millis
            self._calls += 1
            callback = self._kwargs.get("callback")
            if state["first"]:
                state["first"] = False
                if callback:
                    callback.on_data(b"partial-frame")
                return None  # 首帧后停滞 → wait_complete 停滞判定
            if callback:
                callback.on_data(MINI_MP3)
                callback.on_complete()
            return None

        def close(self):
            captured["closed"] = captured.get("closed", 0) + 1

    import types
    import sys

    fake_parent = types.ModuleType("dashscope")
    fake_parent.api_key = None
    fake_module = types.ModuleType("dashscope.audio.tts_v2")
    fake_module.SpeechSynthesizer = FakeSpeechSynthesizer
    fake_module.AudioFormat = FakeAudioFormat
    fake_module.ResultCallback = FakeResultCallback
    monkeypatch.setitem(sys.modules, "dashscope", fake_parent)
    monkeypatch.setitem(sys.modules, "dashscope.audio.tts_v2", fake_module)

    engine = QwenAPI(_make_cfg(STALL_TIMEOUT=0.3))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is not None
    assert len(msg.data) > 0
    assert captured["closed"] >= 2


@pytest.mark.asyncio
async def test_qwen_api_stall_exhausted_returns_none(monkeypatch, clean_env):
    """首帧后停滞且重试仍停滞 → 返回 None（快速失败，不无限阻塞）。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, mode="stall_after_frame")
    engine = QwenAPI(_make_cfg(STALL_TIMEOUT=0.2))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None
    assert captured["closed"] >= 2


@pytest.mark.asyncio
async def test_qwen_api_silent_total_timeout_returns_none(monkeypatch, clean_env):
    """完全无事件（半开连接，无首帧）→ 总超时兜底返回 None（不无限阻塞）。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, mode="silent")
    engine = QwenAPI(_make_cfg(TIMEOUT_MS=300, RETRIES=0))
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None
    assert captured["timeout_millis"] == 300


@pytest.mark.asyncio
async def test_qwen_api_empty_audio_returns_none(monkeypatch, clean_env):
    """on_complete 但无任何音频帧 → 空音频报错 → 返回 None。"""
    captured = {}
    _fake_synthesizer_factory(monkeypatch, captured, mode="empty")
    engine = QwenAPI(_make_cfg())
    msg = await engine.run(TextMessage(data="测试"))
    assert msg is None


@pytest.mark.asyncio
async def test_qwen_api_http_infer(monkeypatch, client, clean_env):
    """HTTP 链路（POST /adh/tts/v0/infer → QwenAPI 引擎）全链路返回 WAV。"""
    captured = {}
    monkeypatch.setenv("QWEN_API_KEY", "http-test-key")
    _fake_synthesizer_factory(monkeypatch, captured, result=MINI_MP3)
    resp = await client.post(
        "/adh/tts/v0/infer",
        json={"engine": "QwenAPI", "data": "欢迎来到数字人系统"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["format"] == "wav"
    assert body["sampleRate"] == 24000
    assert body["sampleWidth"] == 2
    audio = base64.b64decode(body["data"])
    assert len(audio) > 10
    assert captured["call_text"] == "欢迎来到数字人系统"


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
