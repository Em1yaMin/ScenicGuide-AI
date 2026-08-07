# -*- coding: utf-8 -*-
'''
@File    :   qwenTTS.py
@Author  :   pi agent

Qwen（阿里云百炼 DashScope）语音合成引擎。

- 走 dashscope SDK 的 SpeechSynthesizer（WebSocket 协议，
  wss://dashscope.aliyuncs.com/api-ws/v1/inference）
- 实测（2026-08-07）：模型 qwen-audio-3.0-tts-flash、音色
  qwen-audio-3.0-tts-flash-longtongxuxian（龙彤旭弦，活泼灵动女声）
- SDK 不传 callback 时 call() 同步返回完整音频 bytes（mp3）：
  与 minimaxTTS 契约一致——mp3 经 mp3ToWav 转 wav 交给前端
- 失败/未配置 key/dashscope 未安装：记录错误日志并返回 None（不抛错，
  与现有引擎降级行为一致）
- API Key 解析顺序：环境变量 QWEN_API_KEY（部署推荐，key 不入库）
  → 配置文件 API_KEY（占位）
'''

import os
import asyncio
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

            text = input.data
            if not text:
                logger.warning("[TTS] QwenAPI: 输入文本为空")
                return None

            logger.debug(f"[TTS] QwenAPI input: {text[:50]}...")

            def _synthesize() -> bytes:
                # SDK 内部为同步 WebSocket 阻塞调用，放入线程池执行避免阻塞事件循环；
                # 不传 callback → call() 同步返回完整 mp3 bytes。
                # 注意：dashscope SDK 初始化时读全局 dashscope.api_key（None 会抛
                # InputRequired），必须先设置再构造 synthesizer（实测 2026-08-07）。
                import dashscope

                dashscope.api_key = API_KEY
                synthesizer = SpeechSynthesizer(
                    model=MODEL,
                    voice=VOICE,
                    format=AudioFormat.MP3_24000HZ_MONO_256KBPS,
                    url=WS_URL,
                    headers={"Authorization": f"Bearer {API_KEY}"},
                )
                audio = synthesizer.call(text)
                if not audio:
                    raise RuntimeError("DashScope TTS 返回空音频")
                return audio

            mp3_bytes = await asyncio.to_thread(_synthesize)
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
