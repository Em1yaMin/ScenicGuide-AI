# -*- coding: utf-8 -*-
'''
@File    :   minimaxTTS.py
@Author  :   pi agent
'''

from ..builder import TTSEngines
from ..engineBase import BaseEngine
from typing import Optional
from digitalHuman.utils import httpxAsyncClient
from digitalHuman.utils import logger
from digitalHuman.utils import TextMessage, AudioMessage, AudioFormatType
from digitalHuman.utils.audio import mp3ToWav

__all__ = ["MiniMaxAPI"]

@TTSEngines.register("MiniMaxAPI")
class MiniMaxAPI(BaseEngine):

    def checkKeys(self):
        return ["API_KEY", "GROUP_ID"]

    async def run(self, input: TextMessage, **kwargs) -> Optional[AudioMessage]:
        try:
            API_KEY = self.cfg.API_KEY
            GROUP_ID = self.cfg.GROUP_ID
            VOICE_ID = self.cfg.VOICE_ID if "VOICE_ID" in self.cfg else "male-qn-qingse"
            MODEL = self.cfg.MODEL if "MODEL" in self.cfg else "speech-2.8-turbo"
            SAMPLE_RATE = self.cfg.SAMPLE_RATE if "SAMPLE_RATE" in self.cfg else 32000

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {API_KEY}'
            }
            payload = {
                "model": MODEL,
                "text": input.data,
                "stream": False,
                "voice_setting": {
                    "voice_id": VOICE_ID,
                    "speed": 1,
                    "vol": 1,
                    "pitch": 0
                },
                "audio_setting": {
                    "sample_rate": SAMPLE_RATE,
                    "bitrate": 128000,
                    "format": "mp3",
                    "channel": 1
                }
            }

            logger.debug(f"[TTS] MiniMax input: {input.data[:50]}...")
            resp = await httpxAsyncClient.post(
                "https://api.minimaxi.com/v1/t2a_v2",
                json=payload,
                headers=headers
            )
            if resp.status_code != 200:
                logger.error(f"[TTS] MiniMax API error: status={resp.status_code}, body={resp.text[:200]}")
                raise RuntimeError(f"status_code: {resp.status_code}")

            result = resp.json()
            base_resp = result.get("base_resp", {})
            if base_resp.get("status_code") != 0:
                logger.error(f"[TTS] MiniMax API error: {base_resp}")
                raise RuntimeError(f"MiniMax error: {base_resp.get('status_msg', 'unknown')}")

            hex_audio = result.get("data", {}).get("audio", "")
            if not hex_audio:
                logger.error(f"[TTS] MiniMax returned empty audio")
                return None

            # hex -> bytes (MP3) -> WAV
            mp3_bytes = bytes.fromhex(hex_audio)
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
            logger.error(f"[TTS] MiniMax engine run failed: {e}", exc_info=True)
            return None
