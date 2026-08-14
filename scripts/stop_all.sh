#!/usr/bin/env bash
# =============================================================================
# stop_all.sh — 停止四服务（后端 8000 / 前端 5173 / ADH 8001 / ADH-Web 3000）
#
# 用法：
#   scripts/stop_all.sh                  # 停止全部四服务
#   scripts/stop_all.sh backend          # 只停止指定服务（可多个：backend frontend）
#   scripts/stop_all.sh help             # 打印用法
#
# 停止策略（防误杀无关进程）：
#   1. PID 文件优先：/tmp/sg-<服务>.pid（start_all.sh 写入）；要求进程存活
#      且 cmdline 匹配该服务签名（uvicorn / vite / main.py / next），
#      防止 PID 被系统回收复用后误杀无辜进程
#   2. 端口兜底：监听目标端口的进程，同样必须 cmdline 签名匹配才终止
#      （端口 + 签名双条件，不误杀占用同端口的其他程序）
#   3. TERM 优雅退出 → 最多等 10s 端口释放 → 仍占用则 KILL
#
# 退出码：0=全部停止/本就未运行；1=存在停止失败或参数非法
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

SG_USAGE='用法: scripts/stop_all.sh [backend|frontend|adh|adhweb ...]（默认停止全部四服务）'

# 停止单个服务（见文件头「停止策略」）
sg_stop_service() {
    local name="$1" port sig pidfile pids="" pid
    port="$(sg_port "$name")"
    sig="$(sg_cmdline_signature "$name")"
    pidfile="$(sg_pid_file "$name")"

    # 1) PID 文件优先（进程存活 + 签名匹配）
    if [ -f "$pidfile" ]; then
        pid="$(cat "$pidfile" 2>/dev/null || true)"
        if [ -n "$pid" ] && [ -d "/proc/$pid" ] && sg_cmdline "$pid" | grep -Eq "$sig"; then
            pids="$pid"
        fi
    fi
    # 2) 端口兜底（监听该端口且签名匹配的进程）
    if [ -z "$pids" ]; then
        for pid in $(sg_pids_on_port "$port"); do
            if sg_cmdline "$pid" | grep -Eq "$sig"; then
                pids="$pids $pid"
            fi
        done
    fi

    if [ -z "$pids" ]; then
        rm -f "$pidfile"
        echo "[未运行] $name（端口 $port 无匹配进程）"
        return 0
    fi

    # 3) TERM → 等待端口释放 → KILL
    for pid in $pids; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    local i=0
    while [ "$i" -lt 10 ] && sg_port_listening "$port"; do
        sleep 1
        i=$((i + 1))
    done
    if sg_port_listening "$port"; then
        for pid in $pids; do
            kill -KILL "$pid" 2>/dev/null || true
        done
        sleep 1
    fi
    if sg_port_listening "$port"; then
        echo "[失败] $name（端口 $port 仍被占用，进程: $(sg_pids_on_port "$port" | tr '\n' ' ')）" >&2
        return 1
    fi

    rm -f "$pidfile"
    echo "[已停止] $name（pid:${pids}，端口 $port 已释放）"
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
    sg_stop_service "$svc" || failed=1
done

if [ "$failed" -eq 0 ]; then
    echo "完成：全部服务已停止"
else
    echo "完成：存在停止失败项（见上方输出）" >&2
fi
exit "$failed"
