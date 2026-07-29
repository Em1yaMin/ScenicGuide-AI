# -*- coding: utf-8 -*-
'''
@File    :   __init__.py
@Author  :   一力辉 
'''

from .baiduTTS import BaiduAPI
from .edgeTTS import EdgeAPI
from .difyTTS import DifyAPI
from .difyTTSWorkflow import DifyTTSW
from .minimaxTTS import MiniMaxAPI
from .ttsFactory import TTSFactory

__all__ = ['TTSFactory']