"""认证端点限流测试（批次6 C3 安全加固）。

覆盖（对应任务验收标准）:
- 限流器单测：失败达阈值触发冷却 / 冷却期 blocked / 冷却恢复 / 登录成功清零 /
  滑动窗口过期 / guest 频率限制 / IP+账号维度隔离
- HTTP 集成：登录爆破 5 次失败后 429（含 Retry-After）、注册 409 计数、
  guest 频率限制、其他端点（对话/健康检查）不被误伤、关闭开关后不限制
"""
from typing import AsyncGenerator

import pytest
from httpx import AsyncClient, ASGITransport

from app.core.rate_limiter import AuthRateLimiter


# ── 限流器纯逻辑（可控时钟） ────────────────────────────────────────────


class _FakeClock:
    """可控单调时钟：测试手动推进时间。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_limiter(**kwargs) -> tuple[AuthRateLimiter, _FakeClock]:
    clock = _FakeClock()
    limiter = AuthRateLimiter(
        max_failures=3, window_seconds=60, cooldown_seconds=900, now=clock, **kwargs
    )
    return limiter, clock


def test_failures_trigger_cooldown():
    """连续失败达阈值后进入冷却；冷却期 is_blocked=True。"""
    limiter, _ = _make_limiter()
    assert not limiter.is_blocked("1.2.3.4", "alice")
    limiter.record_failure("1.2.3.4", "alice")
    assert not limiter.is_blocked("1.2.3.4", "alice")
    limiter.record_failure("1.2.3.4", "alice")
    assert not limiter.is_blocked("1.2.3.4", "alice")
    # 第 3 次失败触发冷却
    triggered = limiter.record_failure("1.2.3.4", "alice")
    assert triggered is True
    assert limiter.is_blocked("1.2.3.4", "alice")
    assert limiter.cooldown_remaining("1.2.3.4", "alice") > 0


def test_cooldown_auto_recovers():
    """冷却期结束后自动恢复（无需人工重置）。"""
    limiter, clock = _make_limiter()
    for _ in range(3):
        limiter.record_failure("1.2.3.4", "alice")
    assert limiter.is_blocked("1.2.3.4", "alice")
    clock.advance(901)
    assert not limiter.is_blocked("1.2.3.4", "alice")
    assert limiter.cooldown_remaining("1.2.3.4", "alice") == 0


def test_failures_expire_outside_window():
    """滑动窗口：窗口外（60s 前）的失败不计入，不会触发冷却。"""
    limiter, clock = _make_limiter()
    limiter.record_failure("1.2.3.4", "alice")  # t=1000
    clock.advance(61)
    limiter.record_failure("1.2.3.4", "alice")  # t=1061，窗口外
    assert not limiter.is_blocked("1.2.3.4", "alice")
    limiter.record_failure("1.2.3.4", "alice")
    limiter.record_failure("1.2.3.4", "alice")  # 3 次均在窗口内 → 触发
    assert limiter.is_blocked("1.2.3.4", "alice")


def test_success_clears_failures():
    """登录成功清零该维度失败计数与冷却（防误伤正常用户）。"""
    limiter, _ = _make_limiter()
    limiter.record_failure("1.2.3.4", "alice")
    limiter.record_failure("1.2.3.4", "alice")
    limiter.record_success("1.2.3.4", "alice")
    assert not limiter.is_blocked("1.2.3.4", "alice")
    # 清零后重新计数：2 次失败不触发，第 3 次才触发
    limiter.record_failure("1.2.3.4", "alice")
    limiter.record_failure("1.2.3.4", "alice")
    assert not limiter.is_blocked("1.2.3.4", "alice")
    limiter.record_failure("1.2.3.4", "alice")
    assert limiter.is_blocked("1.2.3.4", "alice")


def test_ip_account_dimension_isolation():
    """IP + 账号双维度：不同 IP / 不同账号互不影响。"""
    limiter, _ = _make_limiter()
    for _ in range(3):
        limiter.record_failure("1.2.3.4", "alice")
    # 同 IP 不同账号不受影响
    assert not limiter.is_blocked("1.2.3.4", "bob")
    # 不同 IP 同账号不受影响
    assert not limiter.is_blocked("5.6.7.8", "alice")
    # 同 IP 同账号被限
    assert limiter.is_blocked("1.2.3.4", "alice")


def test_guest_frequency_limit():
    """guest 纯 IP 频率限制：窗口内超限 blocked，下个窗口恢复。"""
    limiter, clock = _make_limiter(guest_max_per_window=3, guest_window_seconds=60)
    assert not limiter.is_guest_blocked("1.2.3.4")
    limiter.record_guest_request("1.2.3.4")
    limiter.record_guest_request("1.2.3.4")
    limiter.record_guest_request("1.2.3.4")
    assert limiter.is_guest_blocked("1.2.3.4")
    # 其他 IP 不受影响
    assert not limiter.is_guest_blocked("5.6.7.8")
    clock.advance(61)
    assert not limiter.is_guest_blocked("1.2.3.4")


def test_cooldown_does_not_extend_during_block():
    """冷却期内重复失败不延长冷却（防并发请求反复重置起点）。"""
    limiter, clock = _make_limiter()
    for _ in range(3):
        limiter.record_failure("1.2.3.4", "alice")
    remaining_before = limiter.cooldown_remaining("1.2.3.4", "alice")
    clock.advance(60)
    limiter.record_failure("1.2.3.4", "alice")  # 冷却中记录失败 → 忽略
    remaining_after = limiter.cooldown_remaining("1.2.3.4", "alice")
    # 冷却起点不变：剩余时间只减了 60s（±1s 舍入）
    assert remaining_after <= remaining_before - 59


# ── HTTP 集成（替换 auth 模块限流器为小参数实例） ───────────────────────


@pytest.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """带认证路由限流替换的测试客户端。"""
    from app.core.database import get_db
    from app.main import app
    from app.routers import auth as auth_router

    # 小参数限流器：3 次失败/60s 窗口/900s 冷却；guest 5 次/分钟
    test_limiter = AuthRateLimiter(
        max_failures=3, window_seconds=60, cooldown_seconds=900, guest_max_per_window=5
    )
    original = auth_router._limiter
    auth_router._limiter = test_limiter

    async def override_get_db():
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    auth_router._limiter = original


@pytest.mark.asyncio
async def test_login_bruteforce_gets_429(client: AsyncClient):
    """爆破：同一账号连续 3 次错误密码后第 4 次请求返回 429（含明确提示 + Retry-After）。"""
    payload = {"username": "victim", "password": "wrong-pass"}
    for _ in range(3):
        resp = await client.post("/api/v1/auth/login", json=payload)
        assert resp.status_code == 401
    resp = await client.post("/api/v1/auth/login", json=payload)
    assert resp.status_code == 429
    data = resp.json()
    assert "尝试过于频繁" in data["detail"]
    assert "秒后重试" in data["detail"]
    assert int(resp.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_register_duplicate_counts_as_failure(client: AsyncClient):
    """注册 409（重名）计入失败次数，达阈值后注册接口同样 429。"""
    payload = {"username": "dupuser", "password": "secret123"}
    r1 = await client.post("/api/v1/auth/register", json=payload)
    assert r1.status_code == 201
    for _ in range(3):
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 409
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_login_success_resets_failures(client: AsyncClient):
    """输错 2 次后正确登录成功 → 失败计数清零，后续错误不触发 429。"""
    payload = {"username": "lucky", "password": "secret123"}
    await client.post("/api/v1/auth/register", json=payload)
    for _ in range(2):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "lucky", "password": "bad"}
        )
        assert resp.status_code == 401
    resp = await client.post("/api/v1/auth/login", json=payload)
    assert resp.status_code == 200
    # 清零后：2 次失败仍 401，不触发 429
    for _ in range(2):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "lucky", "password": "bad"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_guest_rate_limited(client: AsyncClient):
    """guest 端点：窗口内超过 5 次签发返回 429，其余端点不受影响。"""
    for _ in range(5):
        resp = await client.post("/api/v1/auth/guest")
        assert resp.status_code == 200
    resp = await client.post("/api/v1/auth/guest")
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_non_auth_endpoints_not_affected(client: AsyncClient):
    """限流不误伤正常接口：健康检查/对话/首页高亮在限流后仍正常。"""
    # 触发登录冷却
    for _ in range(3):
        await client.post(
            "/api/v1/auth/login", json={"username": "x", "password": "bad"}
        )
    resp = await client.post("/api/v1/auth/login", json={"username": "x", "password": "bad"})
    assert resp.status_code == 429
    # 健康检查
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    # 对话（未登录兜底 visitor，不应被限流误伤；无 LLM 配置时回落 Dify 失败
    # 由全局异常处理器转 200，只要不是 429 即证明未被限流误伤）
    resp = await client.post(
        "/api/v1/dialogue/chat", json={"message": "你好", "conversation_id": ""}
    )
    assert resp.status_code != 429


@pytest.mark.asyncio
async def test_rate_limit_disabled_switch(client: AsyncClient, monkeypatch):
    """AUTH_RATE_LIMIT_ENABLED=false 时认证端点不被限流。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_ENABLED", False)
    payload = {"username": "nobody", "password": "wrong"}
    for _ in range(6):
        resp = await client.post("/api/v1/auth/login", json=payload)
        assert resp.status_code == 401  # 始终 401，绝不 429
    for _ in range(6):
        resp = await client.post("/api/v1/auth/guest")
        assert resp.status_code == 200
