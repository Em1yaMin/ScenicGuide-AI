# -*- coding: utf-8 -*-
'''
@File    :   test_engine_pool.py
@Author  :   一力辉
'''

import logging
import pytest
from yacs.config import CfgNode as CN
from digitalHuman.engine import EnginePool, EngineType
from digitalHuman.engine import enginePool as enginePoolModule


class _StubEngine:
    """模拟引擎实例（构造不触发真实初始化）。"""

    def __init__(self, cfg: CN):
        self.name = cfg.NAME


class _FakeFactory:
    """可配置失败的工厂：failNames 中的引擎 create 时抛错。"""

    def __init__(self, failNames=()):
        self._failNames = set(failNames)

    def create(self, cfg: CN):
        if cfg.NAME in self._failNames:
            raise RuntimeError(f"fake init failed: {cfg.NAME}")
        return _StubEngine(cfg)


def _make_config(failNames=(), default="GoodAPI"):
    cfg = CN(new_allowed=True)
    cfg.ASR = CN(new_allowed=True)
    cfg.LLM = CN(new_allowed=True)
    cfg.TTS = CN(new_allowed=True)
    for engineType in ("ASR", "LLM", "TTS"):
        setattr(
            cfg, engineType,
            CN(
                new_allowed=True,
                init_dict={
                    "SUPPORT_LIST": [
                        CN(new_allowed=True, init_dict={"NAME": "BadAPI"}),
                        CN(new_allowed=True, init_dict={"NAME": "GoodAPI"}),
                    ],
                    "DEFAULT": default,
                },
            ),
        )
    return cfg


@pytest.fixture
def restorePool():
    """测试前清空池，结束后恢复（EnginePool 为单例，conftest 已用真实配置填充）。"""
    pool = EnginePool()
    saved = {t: dict(d) for t, d in pool._pool.items()}
    pool._pool.clear()
    yield pool
    pool._pool.clear()
    pool._pool.update(saved)


def _patch_factories(monkeypatch, failNames):
    fake = _FakeFactory(failNames)
    monkeypatch.setattr(enginePoolModule, "ASRFactory", fake)
    monkeypatch.setattr(enginePoolModule, "LLMFactory", fake)
    monkeypatch.setattr(enginePoolModule, "TTSFactory", fake)
    return fake


def test_non_default_engine_failure_skipped(restorePool, monkeypatch, caplog):
    """非默认引擎初始化失败：跳过该引擎、池继续初始化，默认引擎可用。"""
    _patch_factories(monkeypatch, failNames={"BadAPI"})
    caplog.set_level(logging.ERROR)
    restorePool.setup(_make_config())
    # 默认引擎可用
    engine = restorePool.getEngine(EngineType.LLM, "GoodAPI")
    assert engine is not None and engine.name == "GoodAPI"
    # 失败的引擎被跳过
    assert restorePool.getEngine(EngineType.LLM, "BadAPI") is None
    # 三种类型均受保护
    for engineType in (EngineType.ASR, EngineType.LLM, EngineType.TTS):
        assert restorePool.getEngine(engineType, "GoodAPI") is not None
        assert restorePool.getEngine(engineType, "BadAPI") is None
    # ERROR 日志含引擎名
    assert any(
        "BadAPI" in record.message and "skipped" in record.message
        for record in caplog.records
        if record.levelno == logging.ERROR
    )


def test_default_engine_failure_raises(restorePool, monkeypatch):
    """默认引擎初始化失败：必须显式失败，不静默降级。"""
    _patch_factories(monkeypatch, failNames={"GoodAPI"})
    with pytest.raises(RuntimeError, match="fake init failed: GoodAPI"):
        restorePool.setup(_make_config(default="GoodAPI"))


def test_failed_engine_in_middle_keeps_others(restorePool, monkeypatch):
    """失败引擎位于列表中间：前后引擎均正常创建。"""
    fake = _FakeFactory(failNames={"MidAPI"})
    monkeypatch.setattr(enginePoolModule, "ASRFactory", fake)
    cfg = CN(new_allowed=True)
    cfg.ASR = CN(
        new_allowed=True,
        init_dict={
            "SUPPORT_LIST": [
                CN(new_allowed=True, init_dict={"NAME": "FirstAPI"}),
                CN(new_allowed=True, init_dict={"NAME": "MidAPI"}),
                CN(new_allowed=True, init_dict={"NAME": "LastAPI"}),
            ],
            "DEFAULT": "FirstAPI",
        },
    )
    cfg.LLM = CN(new_allowed=True, init_dict={"SUPPORT_LIST": [], "DEFAULT": None})
    cfg.TTS = CN(new_allowed=True, init_dict={"SUPPORT_LIST": [], "DEFAULT": None})
    restorePool.setup(cfg)
    assert restorePool.getEngine(EngineType.ASR, "FirstAPI") is not None
    assert restorePool.getEngine(EngineType.ASR, "MidAPI") is None
    assert restorePool.getEngine(EngineType.ASR, "LastAPI") is not None


def test_unknown_engine_name_returns_none(restorePool, monkeypatch):
    """getEngine 对池中不存在的引擎返回 None（既有语义不变）。"""
    _patch_factories(monkeypatch, failNames=())
    restorePool.setup(_make_config())
    assert restorePool.getEngine(EngineType.LLM, "NotExistAPI") is None
