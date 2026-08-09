"""多环境配置测试（批次8 C7）：APP_ENV 标识 + 健康检查透出。

三档环境（dev/staging/prod）完整差异与切换指引见 doc/deployment-environments.md。
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _settings(**kwargs) -> Settings:
    """构造 Settings（补默认必填的 Dify 校验项，对齐 test_cors 既有模式）。"""
    defaults = {"DIFY_API_URL": "https://dummy/v1", "DIFY_API_KEY": "dummy"}
    defaults.update(kwargs)
    return Settings(**defaults)


def test_app_env_default_is_dev():
    """未配置 APP_ENV 时默认 dev（本机开发，行为与历史一致）。"""
    assert _settings().APP_ENV == "dev"


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_app_env_valid_values(env):
    """三档合法环境均可配置。"""
    assert _settings(APP_ENV=env).APP_ENV == env


@pytest.mark.parametrize("bad", ["production", "PROD", "test", "prod ", " pre"])
def test_app_env_invalid_rejected(bad):
    """非法取值（大写/别名/空白）启动即报错，防「以为在 prod 实际是 dev」误配。"""
    with pytest.raises(ValidationError) as exc:
        _settings(APP_ENV=bad)
    assert "APP_ENV" in str(exc.value)


async def test_health_exposes_app_env():
    """健康检查透出 app_env（供部署验证/监控区分当前环境）。"""
    import httpx

    from app.core.config import settings
    from app.main import app

    # 直接改单例字段（Settings 非 frozen，校验仅在构造时执行）
    settings.APP_ENV = "prod"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["app_env"] == "prod"
