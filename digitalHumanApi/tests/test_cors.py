"""CORS 白名单测试（批次6 C3 安全加固）。

覆盖（对应任务验收标准）:
- 配置解析：默认白名单含 localhost/127.0.0.1/192.168.16.132 各端口（5173/8001/3000）；
  自定义逗号分隔解析
- 中间件行为：白名单 origin 响应携带 CORS 头（含 credentials）；非白名单 origin
  不携带 CORS 头；预检请求同样按白名单放行/拒绝
- 安全约束：allow_credentials=true 与 allow_origins=["*"] 混用必须启动报错
  （Starlette 会把 * 退化为回显任意 origin = 全放开，等于白名单失效）
"""
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.core.config import _DEFAULT_CORS_ORIGINS, _parse_cors_origins


# ── 配置解析（纯函数，不依赖 settings 单例） ────────────────────────────


def test_default_origins_cover_demo_env():
    """默认白名单：localhost/127.0.0.1/192.168.16.132 × 5173/8001/3000（演示环境不锁死）。"""
    origins = _parse_cors_origins(None)
    assert origins == _DEFAULT_CORS_ORIGINS
    for host in ("localhost", "127.0.0.1", "192.168.16.132"):
        for port in (5173, 8001, 3000):
            assert f"http://{host}:{port}" in origins
    assert "*" not in origins


def test_custom_origins_parsed():
    """自定义 CORS_ORIGINS：逗号分隔 + 空白清理。"""
    assert _parse_cors_origins(" http://a.example.com , http://b.example.com ") == [
        "http://a.example.com",
        "http://b.example.com",
    ]
    assert _parse_cors_origins("") == _DEFAULT_CORS_ORIGINS  # 空值回退默认白名单


# ── 中间件行为（独立构造 app，精确控制 origins） ───────────────────────


def _build_app(origins: list[str]) -> FastAPI:
    app = FastAPI()
    # 与 main.py 相同的挂载参数（allow_credentials 固定 true，JWT Bearer 透传需要）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_whitelisted_origin_gets_cors_headers():
    """白名单 origin：响应携带 Access-Control-Allow-Origin（回显该 origin）。"""
    app = _build_app(["http://localhost:5173", "http://192.168.16.132:5173"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/ping", headers={"Origin": "http://localhost:5173"}
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert resp.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_non_whitelisted_origin_no_cors_headers():
    """非白名单 origin：响应不携带任何 Access-Control-Allow-* 头（浏览器侧被拒）。"""
    app = _build_app(["http://localhost:5173"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/ping", headers={"Origin": "http://evil.example.com"})
        assert resp.status_code == 200  # 服务端正常处理
        # 关键安全头：无 Access-Control-Allow-Origin（浏览器据此拒绝跨域读取）。
        # 注：Starlette 在 allow_credentials=True 时会无条件发送
        # Access-Control-Allow-Credentials: true，但没有 allow-origin 配合时
        # 该头不产生任何放行效果，不能据此判定白名单生效。
        assert "access-control-allow-origin" not in resp.headers


@pytest.mark.asyncio
async def test_preflight_whitelisted_origin_allowed():
    """预检请求：白名单 origin 的 OPTIONS 返回 200 + 允许方法。"""
    app = _build_app(["http://localhost:8001"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.options(
            "/ping",
            headers={
                "Origin": "http://localhost:8001",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8001"


@pytest.mark.asyncio
async def test_preflight_non_whitelisted_origin_rejected():
    """预检请求：非白名单 origin 无 CORS 头（浏览器不会放行实际请求）。"""
    app = _build_app(["http://localhost:5173"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.options(
            "/ping",
            headers={
                "Origin": "http://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers


# ── 主应用冒烟（默认配置） ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_main_app_default_whitelist(client):
    """主应用：默认白名单（含 localhost:5173）生效，非白名单 origin 无 CORS 头。"""
    resp = await client.get(
        "/api/v1/health", headers={"Origin": "http://localhost:5173"}
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    resp = await client.get(
        "/api/v1/health", headers={"Origin": "http://attacker.example.com"}
    )
    assert "access-control-allow-origin" not in resp.headers


# ── 安全约束：credentials 与 * 互斥 ─────────────────────────────────────


def test_credentials_with_wildcard_rejected():
    """CORS_ALLOW_CREDENTIALS=true 时 CORS_ORIGINS 含 * 必须拒绝（防全放开）。"""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError) as exc:
        Settings(
            CORS_ORIGINS=["http://a.example.com", "*"],
            CORS_ALLOW_CREDENTIALS=True,
            DIFY_API_URL="https://dummy/v1",
            DIFY_API_KEY="dummy",
        )
    assert "CORS_ORIGINS" in str(exc.value)


def test_credentials_false_allows_wildcard():
    """显式关闭 credentials 后允许 *（无凭证场景的全放开是合法用法）。"""
    from app.core.config import Settings

    s = Settings(
        CORS_ORIGINS=["*"],
        CORS_ALLOW_CREDENTIALS=False,
        DIFY_API_URL="https://dummy/v1",
        DIFY_API_KEY="dummy",
    )
    assert s.CORS_ORIGINS == ["*"]
