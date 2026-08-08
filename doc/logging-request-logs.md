# 结构化日志与请求日志（批次2 C2）

统一日志体系（标准 logging，无新依赖）。配置与实现见 `app/core/logging_config.py`，
测试见 `tests/test_logging.py`。

## 统一格式

所有 logger 经 `configure_logging()`（main.py lifespan Phase 0 调用，替代就地 basicConfig）
统一格式，时间含毫秒：

```
2026-08-08 14:03:25.123 [INFO] app.core.security: JWT_SECRET 未配置，已生成随机临时密钥...
```

- `LOG_FORMAT`: `%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s`
- `LOG_DATEFMT`: `%Y-%m-%d %H:%M:%S`
- 级别：`DEBUG` 开关（`settings.DEBUG`）与历史行为一致——DEBUG 时根级别更详细（DEBUG），
  否则 INFO；`configure_logging(debug=...)` 可显式传参（测试用）。

## 请求日志

每个 HTTP 请求输出一条 `app.request` logger 的结构化 key=value 日志：

```
2026-08-08 14:03:25.456 [INFO] app.request: method=POST path=/api/v1/dialogue/chat status=200 duration_ms=312.4 client=1 ip=127.0.0.1
```

| 字段 | 说明 |
|------|------|
| method / path | HTTP 方法；path 不含 query（防敏感参数泄漏） |
| status | 响应状态码（异常传播路径未发响应头时为 500） |
| duration_ms | 耗时（毫秒，1 位小数） |
| client | 客户端标识：Bearer JWT `sub`（用户 id 或 `guest-xxx`）；`X-Admin-Token` 匹配配置时 `admin`；解析不到为 `-`（不报错） |
| ip | 客户端 IP（不可得时为 `-`） |

级别规则：

- 异常或 5xx → ERROR（异常传播路径带 `exc_info` 堆栈 + `error=<异常类型>`，
  含响应头已发出（status<500，如 SSE 流中断）后中途抛错的情形；
  纯响应路径 `error=server_error`）
- 噪音路径（`/api/v1/health`、`/api/health`、`/docs`、`/redoc`、`/openapi.json`）→ DEBUG
- 其余 → INFO

## 查询方式

```bash
# 请求日志（按状态码/路径/客户端过滤）
grep 'app.request' backend.log | grep 'status=500'
grep 'app.request' backend.log | grep 'client=admin'
grep 'app.request' backend.log | grep 'path=/api/v1/dialogue/chat'
# 异常堆栈
grep -A20 'error=RuntimeError' backend.log
# 某段时间
grep 'app.request' backend.log | grep '^2026-08-08 14:0[0-9]'
```

## 敏感信息红线

密码、token、key、请求体、请求头（Authorization/X-Admin-Token 值）一律不写入日志：
中间件只读请求头做身份解析，不读请求体（SSE/流式安全）、不记 query、不记原始 token 值。

- **uvicorn.access 已抑制**（`configure_logging()` 压到 WARNING）：服务器层自带 access log
  的固定格式会原样记录完整 query string（如 `?token=...&password=...`），与红线冲突且
  与中间件日志重复——请求日志统一由 `app.request` 中间件承担（更丰富且零泄漏）；
  `uvicorn.error` 启动/错误日志不受影响。

## 已知取舍

- 既有 catch-all 异常处理器把未处理的路由异常转为 HTTP 200（body `code=500`），因此
  中间件的 5xx→ERROR 分支对最常见的路由错误路径不触发——此类失败以 `status=200` 出现，
  排查时不能只按 `status=5xx` 过滤；真正的服务端错误仍由 `main.py` 全局异常处理器
  `logger.error(..., exc_info=...)` 落盘（`app.main` logger），两条线索配合定位。
- SSE 流在响应头发出后中途抛错（status 已为 200）：中间件按「异常优先」判 ERROR，
  带 `exc_info` 堆栈 + `error=<异常类型>`，不会被记为成功 INFO 行。

## 中间件挂载

`main.py` 中 `app.add_middleware(RequestLoggingMiddleware)` 最后添加（最外层），
可见 CORS/预检等全部请求；只处理 `http` scope，websocket 直通。
