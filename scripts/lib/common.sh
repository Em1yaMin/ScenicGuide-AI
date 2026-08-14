#!/usr/bin/env bash
# =============================================================================
# common.sh — 四服务运维脚本公共库
#
# 被 start_all.sh / stop_all.sh / health_check.sh / check_env.sh 引用（同一
# scripts/ 目录下的兄弟脚本通过 `source lib/common.sh` 加载；本文件亦可用
# `source scripts/lib/common.sh` 独立引用）。约定：公共函数一律 `sg_` 前缀。
#
# 服务与端口（演示环境部署拓扑，权威说明见 scripts/README.md）：
#   backend  8000  digitalHumanApi      .venv python -m uvicorn app.main:app
#   frontend 5173  digitalHumanVue      node vite.js --host 0.0.0.0（≠ npm run dev）
#   adh      8001  live2d-frontend      venv python main.py（须带 QWEN_API_KEY）
#   adhweb   3000  live2d-frontend/web  node next dev -p 3000（≠ pnpm dev）
#
# 驻留方式：setsid nohup bash -c '...' </dev/null >log 2>&1 & disown（受限 shell
# 下脱离控制终端与 SIGHUP）；PID 文件在 bash -c 内 `echo $$` 写入，随后 exec 使
# 服务进程 PID 不变（PID 文件 = 服务进程本身，stop 可精确命中）。
# =============================================================================

set -euo pipefail

# 仓库根目录（本文件位于 <repo>/scripts/lib/）
SG_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ───────────────────────── 服务注册表 ─────────────────────────
# 服务名 → 端口 / 健康检查路径（ADH 的 FastAPI 未挂根路由，/ 返回 404，
# 故与后端一样用 /docs 探测；前端 / 与 ADH-Web / 均返回 200）
SG_SERVICES=(backend frontend adh adhweb)
SG_LOG_DIR="${SG_LOG_DIR:-/tmp}"       # 日志/PID 目录（可用环境变量覆盖，测试用）

sg_port() {
    case "$1" in
        backend)  echo 8000 ;;
        frontend) echo 5173 ;;
        adh)      echo 8001 ;;
        adhweb)   echo 3000 ;;
        *)        echo "" ;;
    esac
}

sg_health_path() {
    case "$1" in
        backend)  echo "/docs" ;;
        frontend) echo "/" ;;
        adh)      echo "/docs" ;;
        adhweb)   echo "/" ;;
        *)        echo "" ;;
    esac
}

sg_log_file() { echo "$SG_LOG_DIR/sg-$1.log"; }
sg_pid_file() { echo "$SG_LOG_DIR/sg-$1.pid"; }
sg_url()       { echo "http://127.0.0.1:$(sg_port "$1")$(sg_health_path "$1")"; }

sg_valid_service() {
    local s
    for s in "${SG_SERVICES[@]}"; do
        [ "$s" = "$1" ] && return 0
    done
    return 1
}

# ───────────────────────── 探测工具 ─────────────────────────

# 端口是否处于 LISTEN 状态（ss → lsof → bash /dev/tcp 逐级退化，零外部依赖）
sg_port_listening() {
    local port="$1"
    if command -v ss >/dev/null 2>&1; then
        ss -tlnH 2>/dev/null | awk -v p=":$port " 'index($0, p) { found=1 } END { exit !found }' && return 0
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1 && return 0
    fi
    # 最后手段：bash 内置 /dev/tcp（仅能证明可连接）
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
        exec 3>&- 3<&- 2>/dev/null || true
        return 0
    fi
    return 1
}

# 监听指定端口的 PID 列表（一行一个；ss 无权限看 pid 时退化 lsof）
sg_pids_on_port() {
    local port="$1" out=""
    if command -v ss >/dev/null 2>&1; then
        out="$(ss -tlnpH 2>/dev/null | awk -v p=":$port " '
            index($0, p) && match($0, /pid=[0-9]+/) { print substr($0, RSTART+4, RLENGTH-4) }
        ' | sort -un)"
    fi
    if [ -z "$out" ] && command -v lsof >/dev/null 2>&1; then
        out="$(lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sort -un)"
    fi
    if [ -n "$out" ]; then echo "$out"; fi
}

# 进程 cmdline（空格分隔拼接；进程不存在输出空串）
sg_cmdline() { tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null || true; }

# 各服务的 cmdline 匹配签名（端口 + 签名双条件防误杀无关进程）
sg_cmdline_signature() {
    case "$1" in
        backend)  echo 'uvicorn' ;;
        frontend) echo 'vite' ;;
        adh)      echo 'main\.py' ;;
        adhweb)   echo 'next/dist/bin/next' ;;
        *)        echo '' ;;
    esac
}

# HTTP 状态码（curl 缺失时输出 000，调用方退化为端口探测）
sg_http_status() {
    local url="$1" code
    if command -v curl >/dev/null 2>&1; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo 000)"
    else
        code="000"
    fi
    echo "${code:-000}"
}

# HTTP 是否可用（2xx/3xx）；无 curl 时退化为端口探测
sg_http_up() {
    local url="$1" code
    if command -v curl >/dev/null 2>&1; then
        code="$(sg_http_status "$url")"
        [ "$code" -ge 200 ] && [ "$code" -lt 400 ]
    else
        local port="${url##*:}"
        port="${port%%/*}"
        sg_port_listening "$port"
    fi
}

# 等待 HTTP 就绪，超时返回 1（轮询间隔 1s）
sg_wait_http() {
    local url="$1" timeout="${2:-60}" i=0
    while [ "$i" -lt "$timeout" ]; do
        if sg_http_up "$url"; then return 0; fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

# ───────────────────────── .env 解析 ─────────────────────────

# 从 .env 文件提取键值（去除 export 前缀/行内注释/首尾空白/成对引号；
# 键不存在输出空串并返回 1）。注意：绝不回显值内容（可能含密钥），
# 调用方只做存在性/匹配性判断。
sg_env_get() {
    local file="$1" key="$2" line val
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | tail -n 1 || true)"
    [ -n "$line" ] || return 1
    val="${line#*=}"
    val="$(printf '%s' "$val" | sed -e 's/[[:space:]]#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$val" in
        \"*\") val="${val#\"}"; val="${val%\"}" ;;
        \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    echo "$val"
}

# ───────────────────────── 启动前置检查 ─────────────────────────

# 后端 Python 解释器：优先 digitalHumanApi/.venv，缺失时回退系统 python3
# （需可 import uvicorn）；都不可用输出空串返回 1
sg_backend_python() {
    local py="$SG_REPO_ROOT/digitalHumanApi/.venv/bin/python"
    if [ -x "$py" ]; then
        echo "$py"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import uvicorn' >/dev/null 2>&1; then
        echo "python3"
        return 0
    fi
    return 1
}

# 前端 vite 入口（vite 6+ 的 bin/vite.js；直接 node 调用 = npx vite 等价物，
# 且不引入 npx 包装子进程，保证 PID 文件即服务进程）
sg_vite_entry() {
    local entry="$SG_REPO_ROOT/digitalHumanVue/node_modules/vite/bin/vite.js"
    if [ -f "$entry" ]; then
        echo "$entry"
        return 0
    fi
    return 1
}

# ADH 后端 Python（live2d-frontend/venv）与 config.yaml 前置检查
sg_adh_python() {
    local py="$SG_REPO_ROOT/live2d-frontend/venv/bin/python"
    if [ -x "$py" ]; then
        echo "$py"
        return 0
    fi
    return 1
}

# ADH-Web next 入口（AGENTS.md 已知坑：必须绕过 pnpm dev，直接 node 调用）
sg_next_entry() {
    local entry="$SG_REPO_ROOT/live2d-frontend/web/node_modules/next/dist/bin/next"
    if [ -f "$entry" ]; then
        echo "$entry"
        return 0
    fi
    return 1
}

# QWEN_API_KEY：显式环境变量优先，否则从 digitalHumanApi/.env 的
# EMBEDDING_API_KEY 提取（演示部署两 key 同源）；都没有输出空串
sg_qwen_key() {
    local env_file="$SG_REPO_ROOT/digitalHumanApi/.env" key=""
    if [ -n "${QWEN_API_KEY:-}" ]; then
        echo "$QWEN_API_KEY"
        return 0
    fi
    key="$(sg_env_get "$env_file" EMBEDDING_API_KEY || true)"
    echo "$key"
}
