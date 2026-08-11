# -*- coding: utf-8 -*-
'''
@File    :   qwenTTS.py
@Author  :   pi agent

Qwen（阿里云百炼 DashScope）语音合成引擎。

- 走 dashscope SDK 的 SpeechSynthesizer（WebSocket 协议，
  wss://dashscope.aliyuncs.com/api-ws/v1/inference）
- 实测（2026-08-07）：模型 qwen-audio-3.0-tts-flash、音色
  qwen-audio-3.0-tts-flash-longtongxuxian（龙彤旭弦，活泼灵动女声）
- 合成走 SDK 回调（ResultCallback）流式路径：音频帧经 on_data 累积，
  完成事件（on_complete）到达即返回完整 bytes（mp3），与 minimaxTTS
  契约一致——mp3 经 mp3ToWav 转 wav 交给前端
- 失败/未配置 key/dashscope 未安装：记录错误日志并返回 None（不抛错，
  与现有引擎降级行为一致）
- API Key 解析顺序：环境变量 QWEN_API_KEY（部署推荐，key 不入库）
  → 配置文件 API_KEY（占位）

TTS「说话说一半就终止」修复（2026-08-11 船长反馈，偶发）：
- 根因一（无限阻塞）：SDK 同步 call() 默认不传超时——WebSocket 偶发
  中断（网络抖动/服务端瞬断，半开连接）时完成事件永远不会到达，call()
  无限阻塞，后端请求悬挂、前端该句音频永不返回（叠加前端按序音频队列
  即表现为整段语音中途停摆）。修复：改走回调流式路径 + 自研等待循环，
  带「无音频帧停滞检测」（STALL_TIMEOUT，半开连接 ~20s 快速失败）与
  总超时（TIMEOUT_MS）兜底，失败有界、可重试。
- 根因二（截断音频）：服务端 task-failed（合成中途失败）时 SDK 会把
  「已收到的部分音频」直接交回——mp3 截断转出的 wav 就是「说一半」。
  修复：回调流式路径下失败统一经 on_error 上报，部分音频直接丢弃。
- 失败统一重试 RETRIES 次（新建 synthesizer，TTS 幂等；掩盖偶发网络
  抖动）；均失败返回 None，由前端按序队列跳过该句继续播放
  （chatbot.tsx 已同步修复：失败/超时句不再卡死后续句子）。

「大量句子被跳过」修复（2026-08-11 船长反馈，与前端回归一起派发）：
- 根因一（并发触发限流）：前端按标点断句后多句并发请求合成——长回答
  十几句同时打到 DashScope 触发 Throttling.RateQuota，且并发 WebSocket
  连接风暴导致 SDK 高频报「WebSocket connection is not established or
  has been closed」（连接刚建就被服务端关闭/拒绝）。修复：模块级
  asyncio.Lock 串行化合成（同刻最多一个在途连接，与前端 promise 链
  串行化配合，彻底消除并发）。
- 根因二（重试连接未正确重建）：回调流式路径下每次尝试必须独立新建
  synthesizer 并重新连接（旧代码已每次新建，本版显式保证 + 重试前
  增加 RETRY_BACKOFF 退避，给服务端限流/连接恢复留时间）。
'''

import os
import asyncio
import threading
import time
from typing import Optional

from ..builder import TTSEngines
from ..engineBase import BaseEngine
from digitalHuman.utils import logger
from digitalHuman.utils import TextMessage, AudioMessage, AudioFormatType
from digitalHuman.utils.audio import mp3ToWav

__all__ = ["QwenAPI"]

# 默认参数（与 configs/engines/tts/qwenTTS.yaml 保持一致，cfg 未配置时兜底）
DEFAULT_MODEL = "qwen-audio-3.0-tts-flash"
DEFAULT_VOICE = "qwen-audio-3.0-tts-flash-longtongxuxian"
DEFAULT_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
# mp3 输出采样率（AudioMessage.sampleRate 与 mp3ToWav 转出的 wav 一致）
DEFAULT_SAMPLE_RATE = 24000
# 单次合成总超时（毫秒）：长句合成通常数秒~数十秒，60s 足够；主要作用是把
# 「WebSocket 偶发中断导致的无界阻塞」变成有界失败（可重试）
DEFAULT_TIMEOUT_MS = 60000
# 无音频帧到达的停滞判定秒数（半开连接快速失败；正常合成音频帧持续到达，
# 不会误判长句）
DEFAULT_STALL_TIMEOUT = 20.0
# 失败重试次数（TTS 幂等；掩盖偶发网络抖动）
DEFAULT_RETRIES = 1
# 重试退避秒数：重试前等待，给 DashScope 限流/连接恢复留时间（配合
# 合成串行化，避免重试风暴再次触发限流）
DEFAULT_RETRY_BACKOFF = 0.5

# 合成串行化锁（模块级，跨引擎实例共享）：DashScope WebSocket 合成
# 并发会触发 Throttling.RateQuota 限流与「connection is not established」
# 高频报错（#70-5b 实测）；串行化后同刻最多一个在途连接，偶发失败由
# 重试掩盖（与前端 TTS promise 链串行化配合）。用 asyncio.Lock 而非
# threading.Lock：持锁期间 await to_thread 挂起任务而非阻塞事件循环
# （threading.Lock 的阻塞 acquire 会卡死整条事件循环 → 死锁）。
_TTS_SYNTH_LOCK = asyncio.Lock()


@TTSEngines.register("QwenAPI")
class QwenAPI(BaseEngine):

    def checkKeys(self):
        return ["API_KEY"]

    async def run(self, input: TextMessage, **kwargs) -> Optional[AudioMessage]:
        try:
            # dashscope SDK 延迟导入：未安装时仅本引擎降级，不影响其他引擎/应用启动
            try:
                from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat
            except ImportError:
                logger.error(
                    "[TTS] QwenAPI: dashscope SDK 未安装，无法合成（pip install dashscope）"
                )
                return None

            API_KEY = os.environ.get("QWEN_API_KEY") or self.cfg.API_KEY
            if not API_KEY:
                logger.error("[TTS] QwenAPI: 未配置 API Key（环境变量 QWEN_API_KEY 或配置文件 API_KEY）")
                return None

            MODEL = self.cfg.MODEL if "MODEL" in self.cfg else DEFAULT_MODEL
            VOICE = self.cfg.VOICE if "VOICE" in self.cfg else DEFAULT_VOICE
            WS_URL = (
                self.cfg.BASE_WEBSOCKET_API_URL
                if "BASE_WEBSOCKET_API_URL" in self.cfg
                else DEFAULT_WS_URL
            )
            SAMPLE_RATE = (
                self.cfg.SAMPLE_RATE
                if "SAMPLE_RATE" in self.cfg
                else DEFAULT_SAMPLE_RATE
            )
            TIMEOUT_MS = (
                int(self.cfg.TIMEOUT_MS) if "TIMEOUT_MS" in self.cfg else DEFAULT_TIMEOUT_MS
            )
            STALL_TIMEOUT = (
                float(self.cfg.STALL_TIMEOUT)
                if "STALL_TIMEOUT" in self.cfg
                else DEFAULT_STALL_TIMEOUT
            )
            RETRIES = (
                int(self.cfg.RETRIES) if "RETRIES" in self.cfg else DEFAULT_RETRIES
            )
            RETRY_BACKOFF = (
                float(self.cfg.RETRY_BACKOFF)
                if "RETRY_BACKOFF" in self.cfg
                else DEFAULT_RETRY_BACKOFF
            )

            text = input.data
            if not text:
                logger.warning("[TTS] QwenAPI: 输入文本为空")
                return None

            logger.debug(f"[TTS] QwenAPI input: {text[:50]}...")

            def _synthesize_once() -> bytes:
                """单次合成：回调流式累积完整音频，带停滞/总超时检测。

                返回完整 mp3 bytes；失败（task-failed / 停滞 / 超时 / 空音频）
                抛异常由上层重试/降级。SDK 初始化要求先设全局 dashscope.api_key
                （None 会抛 InputRequired，实测 2026-08-07）。

                每次调用独立新建 synthesizer（新 WebSocket 连接）——重试
                前连接已由 finally 关闭，重试即重新建连（修复「重试时连接未
                正确重建」）；整个合成过程持 _TTS_SYNTH_LOCK 串行化（并发
                连接风暴触发限流/连接错误，见模块注释）。
                """
                import dashscope
                from dashscope.audio.tts_v2 import ResultCallback

                dashscope.api_key = API_KEY

                class _ResultCollector(ResultCallback):
                    """流式结果收集器：SDK WebSocket 线程回调，主线程等待/读取。

                    - on_data：累积音频帧并刷新「最后帧时间」（停滞检测依据）
                    - on_complete：task-finished 事件到达（全部音频已收完）
                    - on_error：task-failed 事件到达（部分音频直接丢弃）
                    """

                    def __init__(self):
                        super().__init__()
                        self._lock = threading.Lock()
                        self._audio = bytearray()
                        self._error = None
                        self._complete = False
                        self._last_frame = time.monotonic()
                        self._got_frame = False

                    def on_data(self, data):
                        with self._lock:
                            self._audio += data
                            self._last_frame = time.monotonic()
                            self._got_frame = True

                    def on_complete(self):
                        with self._lock:
                            self._complete = True

                    def on_error(self, message):
                        with self._lock:
                            self._error = str(message)

                    def wait_complete(self, stall_timeout, total_timeout):
                        """阻塞等待合成完成，返回完整音频 bytes。

                        终止条件（任一）：
                        - on_complete 置位 → 成功，返回已累积音频
                        - on_error 置位（task-failed）→ 抛错丢弃部分音频
                        - 停滞：超过 stall_timeout 无任何音频帧（半开连接）
                        - 总超时：超过 total_timeout 秒
                        """
                        start = time.monotonic()
                        while True:
                            with self._lock:
                                if self._error is not None:
                                    raise RuntimeError(
                                        f"DashScope TTS task-failed: {self._error}"
                                    )
                                if self._complete:
                                    audio = bytes(self._audio)
                                    break
                                # 首帧到达前不判停滞（连接/启动窗口由总超时兜底）
                                stall = (
                                    time.monotonic() - self._last_frame
                                    if self._got_frame
                                    else 0.0
                                )
                            if stall >= stall_timeout:
                                raise TimeoutError(
                                    f"DashScope TTS 音频停滞 {stall_timeout:.0f}s（WebSocket 可能中断）"
                                )
                            if time.monotonic() - start >= total_timeout:
                                raise TimeoutError(
                                    f"DashScope TTS 合成总超时 {total_timeout:.0f}s"
                                )
                            time.sleep(0.1)
                        if not audio:
                            raise RuntimeError("DashScope TTS 返回空音频")
                        return audio

                collector = _ResultCollector()
                synthesizer = SpeechSynthesizer(
                    model=MODEL,
                    voice=VOICE,
                    format=AudioFormat.MP3_24000HZ_MONO_256KBPS,
                    url=WS_URL,
                    headers={"Authorization": f"Bearer {API_KEY}"},
                    callback=collector,
                )
                try:
                    # 回调流式路径：call() 立即返回（SDK 内部线程等待完成事件并
                    # 在收尾时关闭连接）；音频经 collector.on_data 累积，由
                    # wait_complete 以停滞/总超时守护等待完整收尾。
                    synthesizer.call(text, timeout_millis=TIMEOUT_MS)
                    return collector.wait_complete(
                        stall_timeout=STALL_TIMEOUT,
                        total_timeout=TIMEOUT_MS / 1000,
                    )
                finally:
                    # 兜底关闭：超时/停滞中止路径 SDK 不会自关，防止连接/线程泄漏
                    try:
                        synthesizer.close()
                    except Exception:
                        pass

            mp3_bytes = None
            for attempt in range(RETRIES + 1):
                if attempt > 0:
                    # 重试前退避：给 DashScope 限流/连接恢复留时间（配合串行化）
                    await asyncio.sleep(RETRY_BACKOFF)
                try:
                    # 串行化：同刻最多一个 DashScope WebSocket 合成连接
                    # （模块级 asyncio 锁，跨引擎实例共享；不阻塞事件循环）
                    async with _TTS_SYNTH_LOCK:
                        mp3_bytes = await asyncio.to_thread(_synthesize_once)
                    break
                except Exception as e:
                    logger.error(
                        f"[TTS] QwenAPI 合成失败（第 {attempt + 1}/{RETRIES + 1} 次）: {e}"
                    )
            if mp3_bytes is None:
                return None

            wav_bytes = mp3ToWav(mp3_bytes)

            message = AudioMessage(
                data=wav_bytes,
                desc=input.data,
                format=AudioFormatType.WAV,
                sampleRate=SAMPLE_RATE,
                sampleWidth=2,
            )
            return message

        except Exception as e:
            logger.error(f"[TTS] QwenAPI engine run failed: {e}", exc_info=True)
            return None
