# -*- coding: utf-8 -*-
'''
@File    :   difyTTSWorkflow.py
@Author  :   一力辉 
'''

from ..builder import TTSEngines
from ..engineBase import BaseEngine
import asyncio
from typing import Optional
from digitalHuman.utils import httpxAsyncClient
from digitalHuman.utils import logger
from digitalHuman.utils import TextMessage, AudioMessage, AudioFormatType
from digitalHuman.utils.audio import mp3ToWav

__all__ = ["DifyTTSW"]


@TTSEngines.register("DifyTTSW")
class DifyTTSW(BaseEngine):
    def setup(self):
        super().setup()
        self.asyncLock = True

    async def run(self, input: TextMessage, **kwargs) -> Optional[AudioMessage]:
        try:
            WORKFLOW_URL = ""
            WORKFLOW_API_KEY = ""
            # 参数填充
            for paramter in self.parameters():
                if paramter['NAME'] == "DIFY_WORKFLOW_URL":
                    WORKFLOW_URL = paramter['DEFAULT'] if paramter['NAME'] not in kwargs else kwargs[paramter['NAME']]
                if paramter['NAME'] == "DIFY_WORKFLOW_API_KEY":
                    WORKFLOW_API_KEY = paramter['DEFAULT'] if paramter['NAME'] not in kwargs else kwargs[paramter['NAME']]

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {WORKFLOW_API_KEY}'
            }
            payload = {
                "inputs": {"text": input.data},
                "response_mode": "blocking",
                "user": "adh"
            }

            logger.debug(f"[TTS] Engine input: {input.data}")
            # 并发阻塞, 防止dify接口因为并发请求而报错
            while not self.asyncLock:
                await asyncio.sleep(0.1)
            self.asyncLock = False

            # Step 1: 触发 Workflow
            resp = await httpxAsyncClient.post(WORKFLOW_URL.rstrip("/") + "/workflows/run", json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"status_code: {resp.status_code}")
            data = resp.json()
            outputs = data.get("data", {}).get("outputs", {})

            # Step 2: 从 outputs 中提取文件 URL
            file_url = None
            for value in outputs.values():
                if isinstance(value, dict) and "url" in value:
                    file_url = value["url"]
                    break
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and "url" in item:
                            file_url = item["url"]
                            break
                    if file_url:
                        break

            if not file_url:
                raise RuntimeError("No file URL found in workflow outputs")

            # 处理相对路径 URL
            if file_url.startswith("/"):
                file_url = WORKFLOW_URL + file_url

            # Step 3: 下载音频文件
            download_resp = await httpxAsyncClient.get(file_url, headers={"Authorization": f"Bearer {WORKFLOW_API_KEY}"})
            if download_resp.status_code != 200:
                raise RuntimeError(f"Download failed: status_code {download_resp.status_code}")

            self.asyncLock = True

            message = AudioMessage(
                data=mp3ToWav(download_resp.content),
                desc=input.data,
                format=AudioFormatType.WAV,
                sampleRate=16000,
                sampleWidth=2,
            )
            return message

        except Exception as e:
            self.asyncLock = True
            logger.error(f"[TTS] Engine run failed: {e}", exc_info=True)
            return None
