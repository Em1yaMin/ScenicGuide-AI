# -*- coding: utf-8 -*-
'''
@File    :   agentApi.py
@Author  :   一力辉 
'''

from .reponse import BaseResponse, Response
from typing import Optional, List
import json
from pydantic import BaseModel
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from digitalHuman.utils import config
from digitalHuman.utils import TextMessage, StepMessage
from digitalHuman.agent import AgentPool

router = APIRouter()
agentPool = AgentPool()

# 步骤帧协议（前端解析见 web/app/lib/comm.ts）：
#   帧格式：\x02{json}\x03，其中 json = {"tool": "...", "label": "..."}
#   回答文本仍按原始文本流透出，不参与帧编码（TTS/逐字渲染依赖不变）
STEP_FRAME_START = "\x02"
STEP_FRAME_END = "\x03"

class AgentInferIn(BaseModel):
    engine: str = "default"
    settings: dict = {}
    streaming: bool = False
    data: str

class AgentInferOut(BaseResponse):
    data: bytes

async def interalError():
    yield "内部错误"

@router.post("/v0/infer", response_model=AgentInferOut, summary="AI Agent Inference")
async def apiAgentInfer(item: AgentInferIn):
    if item.engine.lower() == "default":
        item.engine = config.SERVER.AGENTS.DEFAULT
    response = Response()
    try:
        input = TextMessage(data=item.data)

        async def stream_with_steps():
            # 透出 agent 产出流：StepMessage 编码为步骤帧（前端渲染），
            # 回答文本原样透出（行为不变）
            async for chunk in agentPool.get(item.engine).run(input, item.streaming, **item.settings):
                if isinstance(chunk, StepMessage):
                    frame = json.dumps(
                        {"tool": chunk.tool or "", "label": chunk.label or ""},
                        ensure_ascii=False,
                    )
                    yield STEP_FRAME_START + frame + STEP_FRAME_END
                else:
                    yield chunk

        return StreamingResponse(stream_with_steps(), media_type="text/event-stream")
    except Exception as e:
        response.error(str(e))
        return StreamingResponse(interalError)


class ConversatonIdIn(BaseModel):
    engine: str = "default"
    settings: dict = {}
    streaming: bool = False

class ConversatonIdOut(BaseResponse):
    data: str

@router.post("/v0/conversation_id", response_model=ConversatonIdOut, summary="Get AI Agent Conversion ID")
async def apiAgentInfer(item: ConversatonIdIn):
    if item.engine.lower() == "default":
        item.engine = config.SERVER.AGENTS.DEFAULT
    response = Response()
    try:
        response.data = await agentPool.get(item.engine).createConversation(item.streaming, **item.settings)
    except Exception as e:
        response.data = ""
        response.error(str(e))
    return JSONResponse(content=response.validate(ConversatonIdOut), status_code=200)


class AgentSettingsIn(BaseModel):
    engine: str

class AgentSettingsOut(BaseResponse):
    data: List[dict]

@router.post("/v0/settings", response_model=AgentSettingsOut, summary="Get AI Agent Settings")
async def apiAgentSettings(item: AgentSettingsIn):
    response = Response()
    try:
        response.data = agentPool.get(item.engine).parameters()
    except Exception as e:
        response.error(str(e))
    return JSONResponse(content=response.validate(AgentSettingsOut), status_code=200)


class AgentListOut(BaseResponse):
    data: List[str] = []

@router.get("/v0/list", response_model=AgentListOut, summary="Get AI Agent List")
async def apiAgentList():
    response = Response()
    try:
        response.data = agentPool.list()
    except Exception as e:
        response.error(str(e))
    return JSONResponse(content=response.validate(AgentListOut), status_code=200)


class AgentDefaultOut(BaseResponse):
    data: str

@router.get("/v0/default", response_model=AgentDefaultOut, summary="Get Default AI Agent")
async def apiAgentDefault():
    response = Response()
    try:
        response.data = config.SERVER.AGENTS.DEFAULT
    except Exception as e:
        response.error(str(e))
    return JSONResponse(content=response.validate(AgentDefaultOut), status_code=200)