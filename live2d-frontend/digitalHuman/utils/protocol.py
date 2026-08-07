# -*- coding: utf-8 -*-
'''
@File    :   protocol.py
@Author  :   一力辉 
'''

from enum import Enum
from uuid import uuid4
from typing import Optional
from pydantic import BaseModel, Field

# 支持的输入格式
class AudioFormatType(Enum):
    """
    Format Type
    """
    MP3 = "mp3"
    WAV  = "wav"

    def __str__(self):
        return str(self.value)

class BaseMessage(BaseModel):
    """
    Base Protocol
    """
    id: str = Field(default_factory=lambda: str(uuid4()))

class AudioMessage(BaseMessage):
    data: bytes
    format: AudioFormatType
    sampleRate: int
    sampleWidth: int
    desc: Optional[str] = None


class TextMessage(BaseMessage):
    data: Optional[str] = None
    desc: Optional[str] = None


class StepMessage(BaseMessage):
    """
    Agent 步骤事件（工具调用步骤，如"正在检索知识库…"）。

    由 LangChainAgent 在流式对话中透出给调用方（agentApi 编码为步骤帧
    供前端渲染），不参与回答文本流，不影响 TTS/逐字渲染。
    """
    tool: Optional[str] = None
    label: Optional[str] = None