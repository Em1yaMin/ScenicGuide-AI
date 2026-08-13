# -*- coding: utf-8 -*-
'''
@File    :   test_asr_api.py
@Author  :   一力辉 
'''

import pytest
from httpx import AsyncClient
from digitalHuman.utils import config

# 实际启用的 agent 列表（config.yaml 不存在时回退 config_template.yaml）
_SUPPORTED_AGENTS = {c.NAME for c in config.SERVER.AGENTS.SUPPORT_LIST}


class Test_AGENT_API():
    # ======================== list ==========================
    @pytest.mark.asyncio(scope="session")
    async def test_list(self, version: str, client: AsyncClient):
        url = f"/adh/agent/{version}/list"
        resp = await client.get(url)
        assert resp.status_code == 200
        resp = resp.json()
        assert resp["code"] == 0
        assert len(resp["data"]) >= 1
        # 列表应包含所有已配置的 agent（RepeaterAgent 等仅当配置存在时断言）
        assert _SUPPORTED_AGENTS <= set(resp["data"])

    # ====================== repeater ========================
    @pytest.mark.asyncio(scope="session")
    @pytest.mark.skipif("RepeaterAgent" not in _SUPPORTED_AGENTS, reason="RepeaterAgent 未在配置中启用，跳过")
    async def test_repeater_text_infer(self, version: str, client: AsyncClient):
        url = f"/adh/agent/{version}/infer"
        item = {
            "engine": "RepeaterAgent",
            "settings": {},
            "data": "你好",
        }
        resp = await client.post(url, json=item)
        assert resp.status_code == 200
        resp = resp.json()
        assert resp["code"] == 0
        assert resp["data"] == "你好"