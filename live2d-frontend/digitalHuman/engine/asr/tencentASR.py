# -*- coding: utf-8 -*-
'''
@File    :   tencentASR.py
@Author  :   一力辉 
'''

"""
API文档:
https://cloud.tencent.com/document/product/1093/35646
"""

from ..builder import ASREngines
from ..engineBase import BaseEngine
import base64
from typing import List, Optional
from digitalHuman.utils import AudioMessage, TextMessage
from digitalHuman.utils import logger

__all__ = ["TencentAPI"]

@ASREngines.register("TencentAPI")
class TencentAPI(BaseEngine): 
    def checkKeys(self) -> List[str]:
        return ["AK", "SK", "APPID", "REGION"]
    
    def setup(self):
        from tencentcloud.common import credential
        self._cred = credential.Credential(self.cfg.AK, self.cfg.SK)
        self._region = self.cfg.REGION
    
    async def run(self, input: AudioMessage, **kwargs) -> Optional[TextMessage]:
        try: 
            try:
                from tencentcloud.asr.v20190614 import models, asr_client_async
            except ImportError as e:
                raise RuntimeError(f"tencentcloud-sdk-python-asr is not installed or wrong version: {e}")
            
            req = models.SentenceRecognitionRequest()
            req.EngSerViceType = "16k_zh"
            req.SourceType = 1
            req.VoiceFormat = str(input.format)
            req.Data = base64.b64encode(input.data).decode("utf-8")
            req.DataLen = len(input.data)

            async with asr_client_async.AsrClient(self._cred, self._region) as client:
                resp = await client.SentenceRecognition(req)

            message = TextMessage(data=resp.Result)
            logger.debug(f"[ASR] input format={input.format}, sampleRate={input.sampleRate}, sampleWidth={input.sampleWidth}, dataLen={len(input.data)}, result='{resp.Result}'")
            return message
            
        except Exception as e:
            logger.error(f"[ASR] Engine run failed: {e}", exc_info=True)
            return None
