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
- **状态有界**：内部三个字典条目总数超上限（max_keys，默认 10000）时自动
  清扫过期状态并逐出最旧条目，防攻击者轮换 key 造成内存耗尽。
- **线程安全**：FastAPI 事件循环单线程执行认证端点，但记录/查询可能跨
  request 交错，使用 threading.Lock 保护内部状态（标准库，无新依赖）。
- 配置项见 app/core/config.py（AUTH_RATE_LIMIT_* / AUTH_GUEST_MAX_PER_MINUTE）；
  AUTH_RATE_LIMIT_ENABLED=false 可整体关闭（回退开关）。
"""
import math
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
        max_keys: int = 10000,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.guest_max_per_window = guest_max_per_window
        self.guest_window_seconds = guest_window_seconds
        # 内部状态条目总数上限：写路径超限时清扫过期/逐出最旧，保证内存有界
        self.max_keys = max_keys
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

    def _sweep(self, now: float) -> None:
        """全局惰性清扫（须持锁调用）：条目总数超上限时回收状态，防无界增长。

        先清理已失效条目（滑动窗口外失败/冷却已过/固定窗口外计数），仍超限
        则逐出时间戳最旧的条目兜底（仅在极端造 key 时触发，保证内存有界）。
        """
        total = (
            len(self._failures) + len(self._cooldown_until) + len(self._request_windows)
        )
        if total < self.max_keys:
            return
        expired_failures = [
            k
            for k, ts in self._failures.items()
            if not any(now - t < self.window_seconds for t in ts)
        ]
        for k in expired_failures:
            self._failures.pop(k, None)
        expired_cooldowns = [k for k, until in self._cooldown_until.items() if until <= now]
        for k in expired_cooldowns:
            self._cooldown_until.pop(k, None)
        expired_windows = [
            k
            for k, (start, _) in self._request_windows.items()
            if now - start >= self.guest_window_seconds
        ]
        for k in expired_windows:
            self._request_windows.pop(k, None)
        while (
            len(self._failures) + len(self._cooldown_until) + len(self._request_windows)
            >= self.max_keys
        ):
            candidates = []
            if self._failures:
                key = min(self._failures, key=lambda k: max(self._failures[k]))
                candidates.append(("failures", key, max(self._failures[key])))
            if self._cooldown_until:
                key = min(self._cooldown_until, key=self._cooldown_until.get)
                candidates.append(("cooldown", key, self._cooldown_until[key]))
            if self._request_windows:
                key = min(self._request_windows, key=lambda k: self._request_windows[k][0])
                candidates.append(("window", key, self._request_windows[key][0]))
            if not candidates:
                break
            bucket, key, _ = min(candidates, key=lambda c: c[2])
            if bucket == "failures":
                self._failures.pop(key, None)
            elif bucket == "cooldown":
                self._cooldown_until.pop(key, None)
            else:
                self._request_windows.pop(key, None)

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
            self._sweep(now)
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
        now = self._now()
        key = self._key(ip, account)
        with self._lock:
            self._sweep(now)
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

    def guest_retry_after(self, ip: str) -> int:
        """guest 频率限制窗口剩余秒数（0 = 未限流），429 响应的 Retry-After
        与提示用——随固定窗口位置真实变化，而非固定阈值。"""
        now = self._now()
        with self._lock:
            window = self._request_windows.get(ip)
        if window and now - window[0] < self.guest_window_seconds:
            return max(1, math.ceil(self.guest_window_seconds - (now - window[0])))
        return 0

    def record_guest_request(self, ip: str) -> None:
        """guest 端点放行后记录一次请求（固定窗口计数）。"""
        now = self._now()
        with self._lock:
            self._sweep(now)
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
    """提取客户端 IP：优先 X-Forwarded-For 的**最后一个**值——nginx 反代部署时
    $proxy_add_x_forwarded_for 把真实对端 IP 追加为最后一位（客户端无法伪造，
    多级代理同理取最近一跳）；否则取直连地址（TCP 对端可信）；解析不到时
    兜底 "unknown"。注意：直连 8000 且伪造 XFF 时最后一位仍由客户端控制，
    该场景只能靠不暴露端口或网络层限制兜底。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client:
        return request.client.host
    return "unknown"
