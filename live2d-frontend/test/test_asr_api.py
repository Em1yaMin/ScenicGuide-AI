# -*- coding: utf-8 -*-
'''
@File    :   test_asr_api.py
@Author  :   一力辉 
'''

import base64
import os
import pytest
from httpx import AsyncClient
from digitalHuman.utils import config

# 实际启用的 ASR 引擎列表（config.yaml 不存在时回退 config_template.yaml）
_SUPPORTED_ASR = {c.NAME for c in config.SERVER.ENGINES.ASR.SUPPORT_LIST}
# 测试音频（真实环境由 gitignored 资源提供）
_TEST_AUDIO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test", "src", "audio", "zh.wav")

class Test_ASR_API():
    @pytest.mark.asyncio(scope="session")
    @pytest.mark.skipif("GoogleAPI" not in _SUPPORTED_ASR or not os.path.exists(_TEST_AUDIO), reason="GoogleAPI 未在配置中启用或测试音频缺失，跳过")
    async def test_goodleAPI_infer(self, version: str, client: AsyncClient, wavAudioZh: str):
        url = f"/adh/asr/{version}/infer"
        with open(wavAudioZh, "rb") as f:
            data = base64.b64encode(f.read()).decode('utf-8')
        item = {
            "engine": "GoogleAPI",
            "data": data,
            "format": "wav",
            "sampleRate": "16000",
            "sampleWidth": 2, 
        }
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        resp = resp.json()
        assert resp["code"] == 0
        assert resp["data"] == "我认为跑步最重要的就是给我带来了身体健康"