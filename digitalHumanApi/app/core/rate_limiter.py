"""认证端点进程内限流器（批次6 C3 安全加固，零新依赖）。

仅针对 /api/v1/auth/login|register|guest 认证端点（防爆破），不误伤对话等
正常接口。设计要点：

- **按 IP + 账号双维度**：key = ``{ip}|{account}``，同一账号从不同 IP 爆破
  各自独立计数，同一 IP 换账号爆破也各自独立（组合维度，最小满足需求；
  如需纯 IP 维度封禁可后续扩展）。
- **失败计数 + 冷却**：滑动窗口内失败次数达到阈值（默认 5 次/分钟）即进入
  冷却期（默认 15 分钟），冷却期内该维度直接 429；冷却结束自动恢复。
- **登录成功清零**：账号维度登录成功后清除该维度失败计数与冷却（防误伤
  正常用户：输错几次后正确登录不受影响）。
- **guest 端点频率限制**：guest 签发无失败场景，改用纯 IP 维度的固定窗口
  请求频率限制（默认每分钟 30 次），防 token 刷取滥用。
- **线程安全**：FastAPI 事件循环单线程执行认证端点，但记录/查询可能跨
  request 交错，使用 threading.Lock 保护内部状态（标准库，无新依赖）。
- 配置项见 app/core/config.py（AUTH_RATE_LIMIT_* / AUTH_GUEST_MAX_PER_MINUTE）；
  AUTH_RATE_LIMIT_ENABLED=false 可整体关闭（回退开关）。
"""
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from fastapi import Request

# 全局单例：路由层直接引用；测试通过 reset() / 构造小参数实例替换
class AuthRateLimiter:
    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: int = 60,
        cooldown_seconds: int = 900,
        guest_max_per_window: int = 30,
        guest_window_seconds: int = 60,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.guest_max_per_window = guest_max_per_window
        self.guest_window_seconds = guest_window_seconds
        # 时钟可注入（测试用可控时钟验证冷却恢复；默认 monotonic）
        self._now = now or time.monotonic
        # 失败时间戳滑动窗口（key -> 最近失败时间戳列表）
        self._failures: Dict[str, List[float]] = {}
        # 冷却截止时间（key -> monotonic 时间戳，未过期即 429）
        self._cooldown_until: Dict[str, float] = {}
        # guest 固定窗口请求计数（ip -> (窗口起点, 计数)）
        self._request_windows: Dict[str, Tuple[float, int]] = {}
        self._lock = threading.Lock()

    # ── 内部工具 ──────────────────────────────────────────────────────

    @staticmethod
    def _key(ip: str, account: Optional[str]) -> str:
        """组合维度 key：无账号（guest 端点）时退化为纯 IP 维度。"""
        if account:
            return f"{ip}|{account}"
        return ip

    def _cleanup_failures(self, key: str, now: float) -> None:
        """滑动窗口清理：丢弃窗口外的失败时间戳。"""
        recent = [
            t for t in self._failures.get(key, []) if now - t < self.window_seconds
        ]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)

    # ── 失败计数 + 冷却（login / register 用） ─────────────────────────

    def is_blocked(self, ip: str, account: Optional[str] = None) -> bool:
        """是否处于冷却期（返回 True 时该维度请求应被 429 拒绝）。"""
        now = self._now()
        key = self._key(ip, account)
        with self._lock:
            if self._cooldown_until.get(key, 0) > now:
                return True
            self._cooldown_until.pop(key, None)
            self._cleanup_failures(key, now)
            return False

    def cooldown_remaining(self, ip: str, account: Optional[str] = None) -> int:
        """冷却剩余秒数（0 = 未限流）。429 响应的 Retry-After 与提示用。"""
        now = self._now()
        with self._lock:
            until = self._cooldown_until.get(self._key(ip, account), 0)
        if until > now:
            return int(until - now) + 1
        return 0

    def record_failure(self, ip: str, account: Optional[str] = None) -> bool:
        """记录一次失败；返回 True 表示本次触发冷却（调用方无需处理，429 后续生效）。"""
        now = self._now()
        key = self._key(ip, account)
        with self._lock:
            # 冷却期内不重复计数、不延长冷却（防并发请求反复重置冷却起点）
            if self._cooldown_until.get(key, 0) > now:
                return False
            self._cleanup_failures(key, now)
            fails = self._failures.setdefault(key, [])
            fails.append(now)
            if len(fails) >= self.max_failures:
                self._cooldown_until[key] = now + self.cooldown_seconds
                self._failures.pop(key, None)
                return True
            return False

    def record_success(self, ip: str, account: Optional[str]) -> None:
        """登录/注册成功后清零该维度失败计数与冷却（防误伤正常用户）。"""
        key = self._key(ip, account)
        with self._lock:
            self._failures.pop(key, None)
            self._cooldown_until.pop(key, None)

    # ── guest 频率限制（固定窗口） ────────────────────────────────────

    def is_guest_blocked(self, ip: str) -> bool:
        """guest 端点纯 IP 维度频率检查（固定窗口）。"""
        now = self._now()
        with self._lock:
            window = self._request_windows.get(ip)
            if (
                window
                and now - window[0] < self.guest_window_seconds
                and window[1] >= self.guest_max_per_window
            ):
                return True
            return False

    def record_guest_request(self, ip: str) -> None:
        """guest 端点放行后记录一次请求（固定窗口计数）。"""
        now = self._now()
        with self._lock:
            window = self._request_windows.get(ip)
            if window and now - window[0] < self.guest_window_seconds:
                self._request_windows[ip] = (window[0], window[1] + 1)
            else:
                self._request_windows[ip] = (now, 1)

    def reset(self) -> None:
        """清空全部状态（测试隔离用；生产无需调用）。"""
        with self._lock:
            self._failures.clear()
            self._cooldown_until.clear()
            self._request_windows.clear()


def client_ip(request: Request) -> str:
    """提取客户端 IP：优先 X-Forwarded-For（nginx 反代部署时 client.host 为
    127.0.0.1），否则取直连地址；解析不到时兜底 "unknown"。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
