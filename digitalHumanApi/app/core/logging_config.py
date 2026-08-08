"""统一日志配置与请求日志中间件（批次2 C2 地基：结构化日志）。

设计约束（船长确认）:
- 保留标准 logging 体系，不引入 loguru/structlog 等新依赖
- 统一 Formatter：时间(含毫秒) / 级别 / logger名 / 消息
- 请求日志：每个 HTTP 请求一条结构化 key=value 日志
  （method / path / status / duration_ms / client / ip）
- 敏感信息红线：密码、token、key、请求体、请求头一律不写入日志

入口:
- configure_logging(): main.py lifespan Phase 0 调用（替代就地 basicConfig，
  DEBUG 开关行为与历史一致）
- RequestLoggingMiddleware: main.py app.add_middleware 挂载（最后添加，最外层，
  可见 CORS/预检等全部请求）

测试: tests/test_logging.py；查询方式见 doc/logging-request-logs.md
"""

import logging
import time
from typing import Optional

from fastapi import Request

from app.core.config import settings
from app.core.security import decode_bearer

logger = logging.getLogger(__name__)

# 统一格式：时间(含毫秒) [级别] logger名: 消息
# %(msecs)03d 显式补充毫秒（提供 datefmt 时 logging 默认不再追加毫秒）
LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 请求日志专用 logger（独立命名空间，便于按级别/目标单独调控）
REQUEST_LOGGER_NAME = "app.request"

# 高频噪音路径（健康检查/文档）：请求日志降级为 DEBUG，避免刷屏
_NOISE_PATHS = (
    "/api/v1/health",
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def build_formatter() -> logging.Formatter:
    """构造统一格式的 Formatter（可复用/单测）。"""
    return logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)


def configure_logging(debug: Optional[bool] = None) -> None:
    """应用统一日志格式（main.py lifespan Phase 0 调用）。

    - debug=True → 根级别 DEBUG（更详细），debug=False → INFO，与历史行为一致
    - 不传参时读 settings.DEBUG；测试可显式传参避免依赖环境
    """
    if debug is None:
        debug = settings.DEBUG
    logging.basicConfig(
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
        level=logging.DEBUG if debug else logging.INFO,
        force=True,
    )


def _is_noise_path(path: str) -> bool:
    """高频路径判定：命中则请求日志降级为 DEBUG。"""
    return (
        path in _NOISE_PATHS
        or path.startswith("/docs")
        or path.startswith("/redoc")
    )


def _resolve_client_id(request: Request) -> str:
    """从请求头解析客户端标识（用户 id / 游客 id / admin）。

    - Authorization: Bearer <JWT> → payload.sub（登录用户 id 或 guest-xxx）
    - X-Admin-Token 与配置匹配 → "admin"（仅标记身份，绝不记录 token 本身）
    - 解析不到 → "-"（不报错、不告警；认证接口自会 401）
    """
    authorization = request.headers.get("authorization")
    if authorization:
        payload = decode_bearer(authorization)
        if payload and payload.get("sub"):
            return str(payload["sub"])
        # 无效/过期 token：不在此告警（对话接口有专属告警），按匿名处理
        return "-"
    if settings.ADMIN_API_TOKEN:
        admin_token = request.headers.get("x-admin-token")
        if admin_token and admin_token == settings.ADMIN_API_TOKEN:
            return "admin"
    return "-"


class RequestLoggingMiddleware:
    """FastAPI HTTP 请求日志中间件：每个请求输出一条结构化日志。

    字段: method / path / status / duration_ms / client / ip
    - 异常或 5xx 记 ERROR（异常传播路径带 exc_info 堆栈；响应路径无异常仅 ERROR 标记）
    - 高频噪音路径（健康检查/文档）降级为 DEBUG
    - 只读请求头不读请求体（SSE/流式安全），不记录任何敏感信息
    """

    def __init__(self, app):
        self.app = app
        self.logger = logging.getLogger(REQUEST_LOGGER_NAME)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        start_time = time.perf_counter()
        status_code = 500  # 默认值：异常传播路径下可能未发出响应头
        exc_info: Optional[BaseException] = None

        async def _send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapper)
        except Exception as exc:
            exc_info = exc
            raise
        finally:
            self._log(request, status_code, start_time, exc_info)

    def _log(
        self,
        request: Request,
        status_code: int,
        start_time: float,
        exc_info: Optional[BaseException],
    ) -> None:
        duration_ms = (time.perf_counter() - start_time) * 1000
        client = _resolve_client_id(request)
        client_ip = request.client.host if request.client else "-"
        fields = (
            f"method={request.method} "
            f"path={request.url.path} "
            f"status={status_code} "
            f"duration_ms={duration_ms:.1f} "
            f"client={client} "
            f"ip={client_ip}"
        )
        if exc_info is not None or status_code >= 500:
            if exc_info is not None:
                self.logger.error(
                    "%s error=%s",
                    fields,
                    type(exc_info).__name__,
                    exc_info=exc_info,
                )
            else:
                self.logger.error("%s error=server_error", fields)
        elif _is_noise_path(request.url.path):
            self.logger.debug("%s", fields)
        else:
            self.logger.info("%s", fields)
