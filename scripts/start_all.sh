#!/usr/bin/env bash
# =============================================================================
# start_all.sh — 一键启动四服务（后端 8000 / 前端 5173 / ADH 8001 / ADH-Web 3000）
#
# 用法：
#   scripts/start_all.sh                 # 启动全部四服务
#   scripts/start_all.sh backend         # 只启动指定服务（可多个：backend frontend）
#   scripts/start_all.sh help            # 打印用法
#
# 特性与坑位集成：
#   - 幂等：目标端口已被占用 → 视为已在运行，跳过并提示（不重复拉起）
#   - 驻留：setsid nohup bash -c '...' </dev/null >log 2>&1 & disown
#     （受限 shell 下脱离控制终端与 SIGHUP，脚本退出后服务继续运行）
#   - PID 文件：bash -c 内先 `echo $$` 再 exec，PID 文件即服务进程本身
#     （stop_all.sh 据此精确停止；日志 /tmp/sg-<服务>.log、PID /tmp/sg-<服务>.pid）
#   - 前端必须直接调 vite.js（等价 npx vite --host 0.0.0.0；不能 npm run dev——
#     且 npx 会多包一层包装子进程导致 PID 追踪失效）
#   - ADH 必须带 QWEN_API_KEY（自动从 digitalHumanApi/.env 的 EMBEDDING_API_KEY
#     提取；显式导出的 QWEN_API_KEY 优先），否则 TTS 无声——缺失时启动仍继续
#     但输出醒目警告
#   - ADH-Web 必须直接调 next（绕过 pnpm dev bug，见 AGENTS.md「ADH 本地数字人」）
#   - 后端优先 digitalHumanApi/.venv，缺失回退系统 python3（需可 import uvicorn）
#
# 退出码：0=全部成功/已跳过；1=任一服务启动失败或参数非法
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

SG_USAGE='用法: scripts/start_all.sh [backend|frontend|adh|adhweb ...]（默认启动全部四服务）'

# 各服务启动等待就绪的超时（秒）；ADH-Web 首次编译较慢
sg_wait_timeout() {
    case "$1" in
        adhweb)   echo 120 ;;
        *)        echo 60 ;;
    esac
}

# 校验单服务启动前置（依赖缺失给出明确修复指引，返回 1）
sg_precheck() {
    local name="$1" py entry
    case "$name" in
        backend)
            py="$(sg_backend_python || true)"
            if [ -z "$py" ]; then
                echo "  ✗ 未找到后端 Python（digitalHumanApi/.venv 或可 import uvicorn 的 python3）" >&2
                echo "    修复: cd digitalHumanApi && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
                return 1
            fi
            ;;
        frontend)
            entry="$(sg_vite_entry || true)"
            if [ -z "$entry" ]; then
                echo "  ✗ 未找到 vite（digitalHumanVue/node_modules 缺失）" >&2
                echo "    修复: cd digitalHumanVue && npm ci" >&2
                return 1
            fi
            ;;
        adh)
            py="$(sg_adh_python || true)"
            if [ -z "$py" ]; then
                echo "  ✗ 未找到 ADH Python（live2d-frontend/venv 缺失）" >&2
                echo "    修复: cd live2d-frontend && virtualenv -p python3.10 venv && venv/bin/pip install -r requirements.txt" >&2
                return 1
            fi
            if [ ! -f "$SG_REPO_ROOT/live2d-frontend/configs/config.yaml" ]; then
                echo "  ✗ 缺少 live2d-frontend/configs/config.yaml（gitignored，不入库）" >&2
                echo "    修复: cp live2d-frontend/configs/config_template.yaml live2d-frontend/configs/config.yaml 并按部署调整" >&2
                return 1
            fi
            ;;
        adhweb)
            entry="$(sg_next_entry || true)"
            if [ -z "$entry" ]; then
                echo "  ✗ 未找到 next（live2d-frontend/web/node_modules 缺失）" >&2
                echo "    修复: cd live2d-frontend/web && npm ci" >&2
                return 1
            fi
            ;;
    esac
    return 0
}

# 启动单个服务：幂等（端口占用即跳过）→ 前置检查 → setsid 驻留 → 等待 HTTP 就绪
sg_start_service() {
    local name="$1" port pidfile logfile inner qwen
    port="$(sg_port "$name")"
    pidfile="$(sg_pid_file "$name")"
    logfile="$(sg_log_file "$name")"

    if sg_port_listening "$port"; then
        echo "[跳过] $name：端口 $port 已被占用（视为已在运行，幂等）"
        return 0
    fi
    if ! sg_precheck "$name"; then
        echo "[失败] $name：前置检查未通过（见上方修复指引）"
        return 1
    fi

    # 组装 bash -c 内部命令：所有变量经 export 透传（避免路径/密钥引号转义地狱），
    # 先写 PID 文件再 exec（exec 后进程 PID 不变，PID 文件即服务进程）
    case "$name" in
        backend)
            export SG_BACKEND_DIR="$SG_REPO_ROOT/digitalHumanApi"
            backend_py="$(sg_backend_python)"
            export SG_BACKEND_PY="$backend_py"
            # shellcheck disable=SC2016  # $$/变量延迟到 bash -c 内展开（有意为之）
            inner='cd "$SG_BACKEND_DIR" && echo $$ > "$SG_PIDFILE" && exec "$SG_BACKEND_PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$SG_PORT"'
            ;;
        frontend)
            export SG_FRONTEND_DIR="$SG_REPO_ROOT/digitalHumanVue"
            vite_entry="$(sg_vite_entry)"
            export SG_VITE_ENTRY="$vite_entry"
            # shellcheck disable=SC2016  # $$/变量延迟到 bash -c 内展开（有意为之）
            inner='cd "$SG_FRONTEND_DIR" && echo $$ > "$SG_PIDFILE" && exec node "$SG_VITE_ENTRY" --host 0.0.0.0'
            ;;
        adh)
            export SG_ADH_DIR="$SG_REPO_ROOT/live2d-frontend"
            adh_py="$(sg_adh_python)"
            export SG_ADH_PY="$adh_py"
            qwen="$(sg_qwen_key)"
            if [ -z "$qwen" ]; then
                echo "  ⚠ QWEN_API_KEY 未配置（digitalHumanApi/.env 的 EMBEDDING_API_KEY 为空）：ADH TTS 将无声" >&2
            fi
            export SG_QWEN_KEY="$qwen"
            # shellcheck disable=SC2016  # $$/变量延迟到 bash -c 内展开（有意为之）
            inner='cd "$SG_ADH_DIR" && echo $$ > "$SG_PIDFILE" && QWEN_API_KEY="$SG_QWEN_KEY" exec "$SG_ADH_PY" main.py'
            ;;
        adhweb)
            export SG_ADHWEB_DIR="$SG_REPO_ROOT/live2d-frontend/web"
            next_entry="$(sg_next_entry)"
            export SG_NEXT_ENTRY="$next_entry"
            # shellcheck disable=SC2016  # $$/变量延迟到 bash -c 内展开（有意为之）
            inner='cd "$SG_ADHWEB_DIR" && echo $$ > "$SG_PIDFILE" && exec node "$SG_NEXT_ENTRY" dev -p "$SG_PORT"'
            ;;
    esac
    export SG_PIDFILE="$pidfile" SG_PORT="$port"

    # 受限 shell 下驻留启动：setsid 脱离会话、nohup 免疫 SIGHUP、disown 移出作业表
    if command -v setsid >/dev/null 2>&1; then
        setsid nohup bash -c "$inner" </dev/null >"$logfile" 2>&1 &
    else
        nohup bash -c "$inner" </dev/null >"$logfile" 2>&1 &
    fi
    disown || true

    echo -n "[启动] $name（端口 $port）... "
    if sg_wait_http "$(sg_url "$name")" "$(sg_wait_timeout "$name")"; then
        echo "就绪：$(sg_url "$name") → HTTP $(sg_http_status "$(sg_url "$name")")"
        echo "       日志: $logfile | PID 文件: $pidfile"
        return 0
    fi
    echo "未就绪（等待超时，请查看日志: $logfile）"
    return 1
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
    sg_start_service "$svc" || failed=1
done

if [ "$failed" -eq 0 ]; then
    echo "完成：全部服务已启动/已在运行"
else
    echo "完成：存在失败项（见上方输出）" >&2
fi
exit "$failed"
