#!/usr/bin/env bash
# =============================================================================
# smoke_ops.sh — 运维脚本冒烟测试（start_all / stop_all / health_check / 单服务/幂等）
#
# 用法：
#   scripts/smoke_ops.sh                 # 冒烟全部服务（依赖齐备者参与测试）
#   scripts/smoke_ops.sh backend adhweb  # 仅冒烟指定服务（可多个）
#
# 覆盖场景（验收标准映射）：
#   1. start_all.sh 一键拉起 → 每服务就绪（HTTP 可访问）
#   2. 重复执行幂等 → 全部「跳过」
#   3. health_check.sh → 全部正常（退出码 0）
#   4. 单服务参数：stop_all.sh <svc> 单独停止 → 端口释放 → start_all.sh <svc> 单独拉起
#   5. stop_all.sh 全部停止 → 端口全部释放、PID 文件清理
#   6. 参数健壮性：未知服务名退出码 1；help 退出码 0
#
# 注意：本脚本会真实启动/停止本机服务（端口 8000/5173/8001/3000），请在
# 干净环境执行；结束前自动停止全部参与服务并清理 PID 文件。
# 退出码：0=全部通过；1=存在失败项。
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"
set +e

PASS=0
FAIL=0

sg_ok()   { printf '  [通过] %s\n' "$1"; PASS=$((PASS + 1)); }
sg_bad()  { printf '  [失败] %s\n' "$1"; FAIL=$((FAIL + 1)); }

sg_expect_exit() { # sg_expect_exit <期望退出码> <说明> <cmd...>
    local want="$1" desc="$2"
    shift 2
    local got=0
    "$@" >/dev/null 2>&1 || got=$?
    if [ "$got" -eq "$want" ]; then
        sg_ok "$desc（退出码 $got）"
    else
        sg_bad "$desc（期望退出码 $want，实际 $got）"
    fi
}

# ───────────────────────── 前置：确定参与测试的服务 ─────────────────────────
if [ $# -eq 0 ]; then
    targets=("${SG_SERVICES[@]}")
else
    targets=("$@")
fi
runnable=()
for svc in "${targets[@]}"; do
    if ! sg_valid_service "$svc"; then
        sg_bad "未知服务 '$svc'"
        continue
    fi
    case "$svc" in
        backend)
            if [ -n "$(sg_backend_python || true)" ]; then runnable+=("$svc"); else sg_bad "backend 前置缺失（无可用 Python/uvicorn）"; fi
            ;;
        frontend)
            if [ -n "$(sg_vite_entry || true)" ]; then runnable+=("$svc"); else sg_bad "frontend 前置缺失（digitalHumanVue/node_modules 无 vite）"; fi
            ;;
        adh)
            if [ -n "$(sg_adh_python || true)" ] && [ -f "$SG_REPO_ROOT/live2d-frontend/configs/config.yaml" ]; then
                runnable+=("$svc")
            else
                sg_bad "adh 前置缺失（venv 或 configs/config.yaml 不存在）"
            fi
            ;;
        adhweb)
            if [ -n "$(sg_next_entry || true)" ]; then runnable+=("$svc"); else sg_bad "adhweb 前置缺失（web/node_modules 无 next）"; fi
            ;;
    esac
done
if [ "${#runnable[@]}" -eq 0 ]; then
    echo "无服务可测（前置全部缺失），冒烟终止"
    exit 1
fi
echo "参与冒烟的服务: ${runnable[*]}"
echo ""

# 清理可能残留的旧实例，确保从干净状态开始
"$SCRIPT_DIR/stop_all.sh" "${runnable[@]}" >/dev/null 2>&1 || true
sleep 1

# ───────────────────────── 1. 一键启动 ─────────────────────────
echo "── 阶段1: start_all.sh 一键启动 ──"
start_out="$("$SCRIPT_DIR/start_all.sh" "${runnable[@]}" 2>&1)"
start_rc=$?
if [ "$start_rc" -eq 0 ]; then
    sg_ok "start_all.sh 退出码 0"
else
    sg_bad "start_all.sh 退出码 $start_rc"
fi
for svc in "${runnable[@]}"; do
    if echo "$start_out" | grep -q "\[启动\] $svc（"; then
        if echo "$start_out" | grep -q "\[启动\] $svc（.*就绪："; then
            sg_ok "$svc 启动并就绪"
        else
            sg_bad "$svc 启动但未就绪（输出: $(echo "$start_out" | grep "\[启动\] $svc（")）"
        fi
    elif echo "$start_out" | grep -q "\[跳过\] $svc："; then
        sg_bad "$svc 本应启动却被跳过（端口被占用？）"
    else
        sg_bad "$svc 未见启动输出"
    fi
    if [ -s "$(sg_pid_file "$svc")" ]; then
        sg_ok "$svc PID 文件已写入"
    else
        sg_bad "$svc PID 文件缺失"
    fi
done

# ───────────────────────── 2. 幂等（重复启动） ─────────────────────────
echo "── 阶段2: 重复执行幂等 ──"
idem_out="$("$SCRIPT_DIR/start_all.sh" "${runnable[@]}" 2>&1)"
idem_rc=$?
if [ "$idem_rc" -eq 0 ]; then
    sg_ok "重复 start_all.sh 退出码 0"
else
    sg_bad "重复 start_all.sh 退出码 $idem_rc"
fi
idem_ok=1
for svc in "${runnable[@]}"; do
    echo "$idem_out" | grep -q "\[跳过\] $svc：" || { idem_ok=0; sg_bad "$svc 重复启动未被跳过（非幂等）"; }
done
[ "$idem_ok" -eq 1 ] && sg_ok "全部服务重复启动均被跳过（幂等）"

# ───────────────────────── 3. 健康检查 ─────────────────────────
echo "── 阶段3: health_check.sh ──"
hc_out="$("$SCRIPT_DIR/health_check.sh" "${runnable[@]}" 2>&1)"
hc_rc=$?
echo "$hc_out"
if [ "$hc_rc" -eq 0 ]; then
    sg_ok "health_check.sh 全部正常（退出码 0）"
else
    sg_bad "health_check.sh 退出码 $hc_rc"
fi

# ───────────────────────── 4. 单服务参数（停一个再拉一个） ─────────────────────────
echo "── 阶段4: 单服务参数 ──"
first="${runnable[0]}"
first_port="$(sg_port "$first")"
stop1_out="$("$SCRIPT_DIR/stop_all.sh" "$first" 2>&1)"
stop1_rc=$?
if [ "$stop1_rc" -eq 0 ] && echo "$stop1_out" | grep -q "\[已停止\] $first（"; then
    sg_ok "stop_all.sh $first 单独停止"
else
    sg_bad "stop_all.sh $first 停止失败（退出码 $stop1_rc）"
fi
if sg_port_listening "$first_port"; then
    sg_bad "$first 端口 $first_port 未释放"
else
    sg_ok "$first 端口 $first_port 已释放"
fi
start1_out="$("$SCRIPT_DIR/start_all.sh" "$first" 2>&1)"
start1_rc=$?
if [ "$start1_rc" -eq 0 ] && echo "$start1_out" | grep -q "\[启动\] $first（.*就绪："; then
    sg_ok "start_all.sh $first 单独拉起并就绪"
else
    sg_bad "start_all.sh $first 单独拉起失败"
fi

# ───────────────────────── 5. 全部停止 ─────────────────────────
echo "── 阶段5: stop_all.sh 全部停止 ──"
stop_out="$("$SCRIPT_DIR/stop_all.sh" "${runnable[@]}" 2>&1)"
stop_rc=$?
if [ "$stop_rc" -eq 0 ]; then
    sg_ok "stop_all.sh 退出码 0"
else
    sg_bad "stop_all.sh 退出码 $stop_rc（输出: $(echo "$stop_out" | grep '失败' || true)）"
fi
all_free=1
for svc in "${runnable[@]}"; do
    if sg_port_listening "$(sg_port "$svc")"; then
        all_free=0
        sg_bad "$svc 端口 $(sg_port "$svc") 未释放"
    else
        sg_ok "$svc 端口 $(sg_port "$svc") 已释放"
    fi
    if [ -f "$(sg_pid_file "$svc")" ]; then
        sg_bad "$svc PID 文件未清理"
    else
        sg_ok "$svc PID 文件已清理"
    fi
done
[ "$all_free" -eq 1 ] && sg_ok "全部端口释放"

# ───────────────────────── 6. 参数健壮性 ─────────────────────────
echo "── 阶段6: 参数健壮性 ──"
sg_expect_exit 1 "未知服务名 → 退出码 1" "$SCRIPT_DIR/start_all.sh" bogus_svc
sg_expect_exit 0 "help → 退出码 0" "$SCRIPT_DIR/start_all.sh" help
sg_expect_exit 0 "health_check help → 退出码 0" "$SCRIPT_DIR/health_check.sh" --help

# ───────────────────────── 汇总 ─────────────────────────
echo ""
echo "========== 冒烟结论: $PASS 通过 / $FAIL 失败 =========="
[ "$FAIL" -eq 0 ]
