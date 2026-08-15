# -*- coding: utf-8 -*-
'''
@File    :   app.py
@Author  :   一力辉 
'''

import os
import uvicorn
from digitalHuman.engine import EnginePool
from digitalHuman.agent import AgentPool
from digitalHuman.server import app
from digitalHuman.utils import config
from digitalHuman.utils import logger

__all__ = ["runServer"]

def runServer():
    enginePool = EnginePool()
    enginePool.setup(config.SERVER.ENGINES)
    agentPool = AgentPool()
    agentPool.setup(config.SERVER.AGENTS)

    # SSL 支持：ADH_SSL_CERTFILE / ADH_SSL_KEYFILE 均非空时启用 HTTPS（宿主机浏览器安全上下文演示需求）
    ssl_certfile = os.environ.get("ADH_SSL_CERTFILE", "").strip()
    ssl_keyfile = os.environ.get("ADH_SSL_KEYFILE", "").strip()
    if ssl_certfile and ssl_keyfile:
        logger.info(f"[runServer] SSL enabled: certfile={ssl_certfile}, keyfile={ssl_keyfile}")
        uvicorn.run(app, host=config.SERVER.IP, port=config.SERVER.PORT, log_level="info",
                    ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile)
    else:
        uvicorn.run(app, host=config.SERVER.IP, port=config.SERVER.PORT, log_level="info")