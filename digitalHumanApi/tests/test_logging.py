"""日志配置与请求日志中间件测试（批次2 C2：结构化日志）。

覆盖:
- 统一 Formatter 格式（时间含毫秒 / 级别 / logger名 / 消息）
- configure_logging 的 DEBUG 开关行为（与历史一致）与格式参数
- 请求日志中间件：每个 HTTP 请求一条结构化日志
  （method / path / status / duration_ms / client）
- 客户端标识解析（JWT 用户 / 游客 / admin；解析不到不报错）
- 噪音路径降级 DEBUG、5xx 异常标记（exc_info）
- 敏感信息红线：token / key / 密码 / 请求体 / query 一律不泄漏
"""

import logging

import pytest

from app.core import logging_config
from app.core.config import settings
from app.core.logging_config import (
    REQUEST_LOGGER_NAME,
    RequestLoggingMiddleware,
    build_formatter,
    configure_logging,
)


def _request_records(caplog):
    """过滤出请求日志 logger 产生的记录（排除其他 logger 噪音）。"""
    return [r for r in caplog.records if r.name == REQUEST_LOGGER_NAME]


def _http_scope(method="GET", path="/", headers=None):
    """构造最小 ASGI http scope（供中间件直测）。"""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }


async def _noop_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _noop_send(message):
    pass


# --- 统一日志格式 ---


def test_build_formatter_includes_all_fields():
    """Formatter 输出：时间(含毫秒) [级别] logger名: 消息。"""
    formatter = build_formatter()
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.msecs = 123.0  # 固定毫秒，验证「时间含毫秒」
    line = formatter.format(record)
    assert ".123 " in line
    assert "[INFO]" in line
    assert "app.test" in line
    assert "hello world" in line


def test_configure_logging_debug_switch(monkeypatch):
    """DEBUG 开关行为与历史一致：debug=True → 根级别 DEBUG，否则 INFO。"""
    captured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: captured.update(kw))

    configure_logging(debug=True)
    assert captured["level"] == logging.DEBUG
    assert captured["format"] == logging_config.LOG_FORMAT
    assert captured["datefmt"] == logging_config.LOG_DATEFMT

    configure_logging(debug=False)
    assert captured["level"] == logging.INFO


def test_configure_logging_suppresses_uvicorn_access(monkeypatch):
    """抑制 uvicorn.access：其固定格式原样记录 query 串（可能含 token），
    与「敏感信息绝不入日志」红线冲突——请求日志统一由中间件承担。"""
    captured = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: captured.update(kw))
    monkeypatch.setattr(
        logging.getLogger("uvicorn.access"),
        "setLevel",
        lambda lv: captured.update(uvicorn_access_level=lv),
    )

    configure_logging(debug=False)
    assert captured["uvicorn_access_level"] == logging.WARNING


# --- 请求日志中间件（经 ASGITransport 全链路） ---


@pytest.mark.asyncio
async def test_request_log_emitted(client, caplog):
    """每个请求输出一条结构化日志：方法/路径/状态码/耗时/客户端标识。"""
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    resp = await client.get("/api/v1/auth/me")  # 无 token → 401（非噪音路径）
    assert resp.status_code == 401

    records = _request_records(caplog)
    assert len(records) == 1
    msg = records[0].message
    assert "method=GET" in msg
    assert "path=/api/v1/auth/me" in msg
    assert "status=401" in msg
    # 耗时为正数（ms）
    assert "duration_ms=" in msg
    duration = float(msg.split("duration_ms=")[1].split(" ")[0])
    assert duration > 0
    # 无 Authorization 头 → 客户端标识为 "-"（解析不到不报错）
    assert "client=-" in msg


@pytest.mark.asyncio
async def test_request_log_client_id_user(client, caplog):
    """Bearer JWT（登录用户）→ client=用户 id。"""
    await client.post(
        "/api/v1/auth/register",
        json={"username": "loguser", "password": "Passw0rd123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "loguser", "password": "Passw0rd123"},
    )
    token = login.json()["access_token"]

    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200

    records = _request_records(caplog)
    assert any(
        "path=/api/v1/auth/me" in r.message and "client=1" in r.message
        for r in records
    )


@pytest.mark.asyncio
async def test_request_log_client_id_guest(client, caplog):
    """Bearer JWT（游客 token）→ client=guest-xxx。"""
    guest = await client.post("/api/v1/auth/guest")
    token = guest.json()["access_token"]
    guest_id = guest.json()["guest_id"]

    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200

    records = _request_records(caplog)
    assert any(
        "path=/api/v1/auth/me" in r.message and f"client={guest_id}" in r.message
        for r in records
    )


@pytest.mark.asyncio
async def test_request_log_client_id_admin(client, caplog, monkeypatch):
    """X-Admin-Token 匹配配置 → client=admin；token 本身绝不入日志。"""
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "admin-secret-xyz")
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    resp = await client.get(
        "/api/admin/knowledge", headers={"X-Admin-Token": "admin-secret-xyz"}
    )
    assert resp.status_code == 200

    records = _request_records(caplog)
    assert any(
        "path=/api/admin/knowledge" in r.message and "client=admin" in r.message
        for r in records
    )
    assert all("admin-secret-xyz" not in r.message for r in records)


@pytest.mark.asyncio
async def test_request_log_client_id_admin_tokens_map(client, caplog, monkeypatch):
    """RBAC（批次6 C3）：经 ADMIN_TOKENS 映射认证的管理员同样标记 client=admin。"""
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "legacy-admin-token")
    monkeypatch.setattr(
        settings, "ADMIN_TOKENS", {"mapped-admin-token": "admin", "op-token": "operator"}
    )
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    resp = await client.get(
        "/api/admin/knowledge", headers={"X-Admin-Token": "mapped-admin-token"}
    )
    assert resp.status_code == 200

    records = _request_records(caplog)
    assert any(
        "path=/api/admin/knowledge" in r.message and "client=admin" in r.message
        for r in records
    )
    # 映射 token 本身同样绝不入日志
    assert all("mapped-admin-token" not in r.message for r in records)


@pytest.mark.asyncio
async def test_request_log_client_invalid_token_not_crash(client, caplog):
    """无效/过期 token：解析不到客户端标识，按 "-" 处理且不报错。"""
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.here"}
    )
    assert resp.status_code == 401  # 认证接口严格 401（行为不变）

    records = _request_records(caplog)
    assert any(
        "path=/api/v1/auth/me" in r.message and "client=-" in r.message
        for r in records
    )


@pytest.mark.asyncio
async def test_noise_path_degraded_to_debug(client, caplog):
    """健康检查等高频路径：INFO 不记录，仅 DEBUG 记录。"""
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert not any(
        "path=/api/v1/health" in r.message for r in _request_records(caplog)
    )

    caplog.clear()
    caplog.set_level(logging.DEBUG, logger=REQUEST_LOGGER_NAME)
    await client.get("/api/v1/health")
    records = _request_records(caplog)
    assert any(
        "path=/api/v1/health" in r.message and "status=200" in r.message
        for r in records
    )


@pytest.mark.asyncio
async def test_sensitive_fields_not_logged(client, caplog):
    """敏感信息红线：token / key / 密码 / 请求体 / query 不泄漏。"""
    caplog.set_level(logging.INFO, logger=REQUEST_LOGGER_NAME)
    # 无效 Bearer token（不应出现在日志里）
    await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer supersecret.jwt.value"}
    )
    # query 携带 token/密码（path 不含 query，不应出现）
    await client.get("/api/v1/auth/me?token=querytoken&password=hunter2")
    # 请求体携带密码（中间件不读请求体）
    await client.post(
        "/api/v1/auth/login", json={"username": "u", "password": "hunter2"}
    )

    all_text = "\n".join(r.message for r in _request_records(caplog))
    assert "supersecret" not in all_text
    assert "querytoken" not in all_text
    assert "hunter2" not in all_text
    assert "password=" not in all_text


# --- 中间件直测：5xx 异常标记 ---


@pytest.mark.asyncio
async def test_middleware_5xx_exception_logs_error(caplog):
    """异常传播路径：ERROR + status=500 + exc_info 堆栈。"""

    async def boom_app(scope, receive, send):
        raise RuntimeError("boom")

    caplog.set_level(logging.ERROR, logger=REQUEST_LOGGER_NAME)
    middleware = RequestLoggingMiddleware(boom_app)
    with pytest.raises(RuntimeError):
        await middleware(_http_scope(path="/boom"), _noop_receive, _noop_send)

    records = _request_records(caplog)
    assert records
    record = records[-1]
    assert record.levelno == logging.ERROR
    assert "method=GET" in record.message
    assert "path=/boom" in record.message
    assert "status=500" in record.message
    assert "error=RuntimeError" in record.message
    assert record.exc_info is not None


@pytest.mark.asyncio
async def test_middleware_exception_after_response_start_logs_error(caplog):
    """响应头已发出（status<500）后中途抛错（如 SSE 流中断）：
    仍记 ERROR + error 类型 + exc_info，不误记为成功 INFO。"""

    async def sse_boom_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {"type": "http.response.body", "body": b"partial", "more_body": True}
        )
        raise RuntimeError("stream broke")

    caplog.set_level(logging.ERROR, logger=REQUEST_LOGGER_NAME)
    middleware = RequestLoggingMiddleware(sse_boom_app)
    with pytest.raises(RuntimeError):
        await middleware(_http_scope(path="/stream"), _noop_receive, _noop_send)

    records = _request_records(caplog)
    assert records
    record = records[-1]
    assert record.levelno == logging.ERROR
    assert "method=GET" in record.message
    assert "path=/stream" in record.message
    assert "status=200" in record.message
    assert "error=RuntimeError" in record.message
    assert record.exc_info is not None


@pytest.mark.asyncio
async def test_middleware_5xx_response_logs_error(caplog):
    """5xx 响应路径（无异常对象）：ERROR + status=500 + error=server_error。"""

    async def err_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 500, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    caplog.set_level(logging.ERROR, logger=REQUEST_LOGGER_NAME)
    await RequestLoggingMiddleware(err_app)(
        _http_scope(path="/err"), _noop_receive, _noop_send
    )

    records = _request_records(caplog)
    assert records
    record = records[-1]
    assert record.levelno == logging.ERROR
    assert "status=500" in record.message
    assert "error=server_error" in record.message
    assert record.exc_info is None
