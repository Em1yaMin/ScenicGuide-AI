#!/usr/bin/env bash
# =============================================================================
# health_check.sh — 四服务健康检查（端口探测 + HTTP 健康检查）
#
# 用法：
#   scripts/health_check.sh              # 检查全部四服务
#   scripts/health_check.sh backend      # 只检查指定服务（可多个）
#   scripts/health_check.sh help         # 打印用法
#
# 探测项：
#   - 端口 LISTEN 状态（ss → lsof → bash /dev/tcp 逐级退化）
#   - HTTP 状态码（curl；无 curl 时退化为端口探测，状态码显示 "-"）
#   健康路径：后端 /docs（FastAPI 文档）、前端 /、ADH /docs（ADH 未挂根路由，
#   / 恒 404，见 scripts/README.md）、ADH-Web /
#
# 退出码：0=全部健康；1=任一服务异常/参数非法
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

SG_USAGE='用法: scripts/health_check.sh [backend|frontend|adh|adhweb ...]（默认检查全部四服务）'

sg_check_service() {
    local name="$1" port url code listening status pids=""
    port="$(sg_port "$name")"
    url="$(sg_url "$name")"
    if command -v ss >/dev/null 2>&1 || command -v lsof >/dev/null 2>&1; then
        pids="$(sg_pids_on_port "$port" | tr '\n' ' ')"
    fi
    if sg_port_listening "$port"; then
        listening="✔"
    else
        listening="✘"
    fi

    if command -v curl >/dev/null 2>&1; then
        code="$(sg_http_status "$url")"
        if [ "$listening" = "✔" ] && [ "$code" -ge 200 ] && [ "$code" -lt 400 ]; then
            status="正常"
        else
            status="异常"
        fi
    else
        code="-"
        if [ "$listening" = "✔" ]; then
            status="正常(仅端口)"
        else
            status="异常"
        fi
    fi
    printf "  %-9s 端口 %-5s 监听 %s  HTTP %-4s  %s%s\n" "$name" "$port" "$listening" "$code" "$status" "${pids:+（pid:${pids% }）}"
    [ "$status" = "异常" ] && return 1
    return 0
}

# ───────────────────────── 主流程 ─────────────────────────
if [ $# -eq 0 ]; then
    targets=("${SG_SERVICES[@]}")
elif [ "$1" = "help" ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "$SG_USAGE"
    exit 0
else
    targets=("$@")
fi

failed=0
for svc in "${targets[@]}"; do
    if ! sg_valid_service "$svc"; then
        echo "错误: 未知服务 '$svc'（可选: ${SG_SERVICES[*]}）" >&2
        failed=1
        continue
    fi
    sg_check_service "$svc" || failed=1
done

if [ "$failed" -eq 0 ]; then
    echo "健康检查通过：全部服务正常"
else
    echo "健康检查未通过：存在异常服务（详见上方状态表）" >&2
fi
exit "$failed"
