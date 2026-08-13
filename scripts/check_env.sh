#!/usr/bin/env bash
# =============================================================================
# check_env.sh — 后端 .env 配置校验（告警不阻断，可作 CI/启动前门禁）
#
# 用法：
#   scripts/check_env.sh                    # 校验 digitalHumanApi/.env
#   scripts/check_env.sh 路径/任意.env      # 校验指定 env 文件（测试/多环境）
#
# 校验项（规则依据见各检查注释与 scripts/README.md「check_env 规则」）：
#   [严重] .env 缺失、APP_ENV 非法、DIFY_API_URL/KEY 为空（config.py 校验器
#          直接拒绝启动）
#   [警告] LLM/Embedding/AMAP/QWEN/管理端/JWT 等关键配置缺失、错配或仍为
#          占位符——不阻断启动，但对应功能静默降级（最典型：EMBEDDING
#          API_STYLE/MODEL 与 BASE 不匹配时 Embedding 静默降级本地 TF-IDF）
#
# 退出码：0=全部通过；1=存在问题（但可继续运行）——符合「告警不阻断」约定。
# 安全：本脚本只输出键名与结论，绝不回显任何值（值可能含密钥）。
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

ENV_FILE="${1:-$SG_REPO_ROOT/digitalHumanApi/.env}"

SG_COUNT_PASS=0
SG_COUNT_WARN=0
SG_COUNT_SEVERE=0

sg_report() { # sg_report <严重|警告|通过> <消息>
    local level="$1" msg="$2"
    case "$level" in
        严重) SG_COUNT_SEVERE=$((SG_COUNT_SEVERE + 1)); printf '  [严重] %s\n' "$msg" ;;
        警告) SG_COUNT_WARN=$((SG_COUNT_WARN + 1));   printf '  [警告] %s\n' "$msg" ;;
        *)    SG_COUNT_PASS=$((SG_COUNT_PASS + 1));   printf '  [通过] %s\n' "$msg" ;;
    esac
}

# 取键值（缺失输出空串）；调用方只判断存在性/匹配性，不回显
sg_get() { sg_env_get "$ENV_FILE" "$1" || true; }

echo "========== check_env.sh：后端 .env 配置校验 =========="
echo "校验文件: $ENV_FILE"
echo ""

# ───────────────────────── 1. 文件与基础档位 ─────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo "  [严重] .env 文件不存在: $ENV_FILE"
    echo "        修复: cp digitalHumanApi/.env.example digitalHumanApi/.env 并按环境填写"
    echo ""
    echo "========== 结论: 0 通过 / 0 警告 / 1 严重（退出码 1）=========="
    exit 1
fi
sg_report 通过 "环境文件存在: $ENV_FILE"

app_env="$(sg_get APP_ENV)"
if [ -z "$app_env" ]; then
    sg_report 警告 "APP_ENV 未配置（默认 dev）；staging/prod 请显式设置"
elif [[ "$app_env" =~ ^(dev|staging|prod)$ ]]; then
    sg_report 通过 "APP_ENV=$app_env（合法档位）"
else
    sg_report 严重 "APP_ENV=$app_env 非法（可选 dev|staging|prod）——后端启动即报错"
fi

# ───────────────────────── 2. Dify（config 校验器强制非空）─────────────────────────
# config.py _validate_dify_config：DIFY_API_URL/DIFY_API_KEY 为空时启动抛
# ValueError（对话双轨回落 + 历史数据接口的配置要求，CI 用占位值注入）
dify_url="$(sg_get DIFY_API_URL)"
dify_key="$(sg_get DIFY_API_KEY)"
if [ -z "$dify_url" ] || [ -z "$dify_key" ]; then
    sg_report 严重 "DIFY_API_URL 或 DIFY_API_KEY 为空——后端启动直接报错（config 校验器强制非空），请填写真实值或占位"
else
    sg_report 通过 "Dify 配置非空（对话双轨回落 + 历史数据接口可用）"
fi

# ───────────────────────── 3. 对话 LLM ─────────────────────────
llm_base="$(sg_get LLM_API_BASE)"
llm_key="$(sg_get LLM_API_KEY)"
if [ -z "$llm_base" ]; then
    sg_report 警告 "LLM_API_BASE 未配置——对话主链路（deepagents）不可用"
elif [[ "$llm_base" =~ ^https?:// ]]; then
    sg_report 通过 "LLM_API_BASE 为合法 http(s) 端点"
else
    sg_report 警告 "LLM_API_BASE 非 http(s) 端点（当前值不合法）"
fi
if [ -z "$llm_key" ]; then
    sg_report 警告 "LLM_API_KEY 未配置——对话将回落 Dify（若 Dify 也未配则对话不可用）；staging/prod 必填"
else
    sg_report 通过 "LLM_API_KEY 已配置"
fi
if [ -z "$(sg_get LLM_FALLBACK_API_KEY)" ]; then
    sg_report 警告 "LLM_FALLBACK_API_KEY 未配置——备用端点不可用（主端点故障时无兜底）"
fi

# ───────────────────────── 4. Embedding 组合（最典型静默降级坑）─────────────────────────
# 已知坑（学习记录）：EMBEDDING_API_STYLE/MODEL 与 EMBEDDING_API_BASE 不匹配时
# （如 worktree/旧副本的半迁移配置），请求发往错误端点 → API 报错 → 静默降级
# 本地 TF-IDF 且无告警。规则（embedding.py 实测）：
#   style=minimax（默认）→ 端点 api.minimaxi.com，模型 embo-*
#   style=openai        → DashScope compatible-mode（dashscope.aliyuncs.com），
#                         模型 qwen*-text-embedding
emb_base="$(sg_get EMBEDDING_API_BASE)"
emb_key="$(sg_get EMBEDDING_API_KEY)"
emb_model="$(sg_get EMBEDDING_MODEL)"
emb_style="$(sg_get EMBEDDING_API_STYLE)"
if [ -z "$emb_style" ]; then
    sg_report 警告 "EMBEDDING_API_STYLE 未配置（默认 minimax，见下方组合校验）"
    emb_style="minimax"
fi
case "$emb_style" in
    minimax)
        if [[ "$emb_base" == *minimax* ]]; then
            sg_report 通过 "Embedding 组合: style=minimax 与 BASE（Minimax 端点）匹配"
        else
            sg_report 警告 "Embedding 错配: EMBEDDING_API_STYLE=minimax 但 BASE 非 Minimax 端点（期望 *minimax*）——向量化将静默降级 TF-IDF"
        fi
        if [ -n "$emb_model" ] && [[ "$emb_model" != *embo* ]]; then
            sg_report 警告 "Embedding 模型疑似错配: EMBEDDING_MODEL=$emb_model（minimax 风格通常为 embo-*）"
        fi
        ;;
    openai)
        if [[ "$emb_base" == *dashscope* ]]; then
            sg_report 通过 "Embedding 组合: style=openai 与 BASE（DashScope compatible-mode）匹配"
        else
            sg_report 警告 "Embedding 错配: EMBEDDING_API_STYLE=openai 但 BASE 非 DashScope 端点（期望 *dashscope*）——向量化将静默降级 TF-IDF"
        fi
        if [ -n "$emb_model" ] && [[ "$emb_model" != *qwen* ]]; then
            sg_report 警告 "Embedding 模型疑似错配: EMBEDDING_MODEL=$emb_model（openai 风格通常为 qwen*-text-embedding）"
        fi
        ;;
    *)
        sg_report 警告 "EMBEDDING_API_STYLE=$emb_style 非法（可选 minimax|openai）——运行时回退 minimax"
        ;;
esac
if [ -z "$emb_key" ]; then
    sg_report 警告 "EMBEDDING_API_KEY 未配置——Embedding 降级本地 TF-IDF（检索质量下降；staging/prod 建议配置）"
else
    sg_report 通过 "EMBEDDING_API_KEY 已配置（远端 Embedding 启用）"
fi

# ───────────────────────── 5. 高德 GPS ─────────────────────────
if [ -z "$(sg_get AMAP_WEB_KEY)" ]; then
    sg_report 警告 "AMAP_WEB_KEY 未配置——GPS 地理编码/路径规划不可用（对话返回 amap_not_configured；staging 演示主推必填）"
else
    sg_report 通过 "AMAP_WEB_KEY 已配置（GPS 工具可用）"
fi

# ───────────────────────── 6. QWEN（ADH TTS 音频键）─────────────────────────
# 已知坑：ADH 的 QWEN_API_KEY 由 start_all.sh 从本 .env 的 EMBEDDING_API_KEY
# 提取（演示部署两 key 同源）；两处皆空时数字人 TTS 无声
if [ -n "${QWEN_API_KEY:-}" ]; then
    sg_report 通过 "QWEN_API_KEY 已由环境变量提供（ADH TTS 可用）"
elif [ -n "$emb_key" ]; then
    sg_report 通过 "QWEN_API_KEY 可经 EMBEDDING_API_KEY 提取（start_all.sh 自动注入 ADH TTS）"
else
    sg_report 警告 "QWEN_API_KEY 与 EMBEDDING_API_KEY 均为空——ADH 数字人 TTS 将无声"
fi

# ───────────────────────── 7. 管理端 / JWT ─────────────────────────
admin_token="$(sg_get ADMIN_API_TOKEN)"
if [ -z "$admin_token" ]; then
    sg_report 警告 "ADMIN_API_TOKEN 未配置——管理端接口鉴权不可用（staging/prod 必填随机长串）"
else
    sg_report 通过 "ADMIN_API_TOKEN 已配置"
fi
if [ -z "$(sg_get JWT_SECRET)" ]; then
    sg_report 警告 "JWT_SECRET 未配置——后端启动生成临时密钥，重启后已签发 token 全部失效（staging/prod 必填）"
else
    sg_report 通过 "JWT_SECRET 已配置"
fi
admin_user="$(sg_get ADMIN_USERNAME)"
if [ -z "$admin_user" ] || [ "$admin_user" = "admin" ]; then
    sg_report 警告 "ADMIN_USERNAME 未配置或为默认演示值 admin——生产环境必须修改"
fi
admin_pwd="$(sg_get ADMIN_PASSWORD)"
if [ -z "$admin_pwd" ] || [ "$admin_pwd" = "admin123456" ]; then
    sg_report 警告 "ADMIN_PASSWORD 未配置或为默认演示值 admin123456——生产环境必须修改"
fi

# ───────────────────────── 8. 占位符全局扫描 ─────────────────────────
# 扫 .env 全部键，值含占位符特征（your- / xxx / placeholder / changeme）时
# 告警并列出键名（不回显值内容）
placeholders=""
while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    key="${line%%=*}"
    key="${key#"export"}"
    key="$(printf '%s' "$key" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    val="$(sg_get "$key")"
    if [ -n "$val" ] && printf '%s' "$val" | grep -Eq 'your-|placeholder|changeme|^xxx$|sk-your'; then
        placeholders="$placeholders $key"
    fi
done < "$ENV_FILE"
if [ -n "$placeholders" ]; then
    sg_report 警告 "以下配置仍为占位符（未替换真实值）:${placeholders}"
else
    sg_report 通过 "未发现占位符残留"
fi

# ───────────────────────── 结论 ─────────────────────────
echo ""
echo "========== 结论: $SG_COUNT_PASS 通过 / $SG_COUNT_WARN 警告 / $SG_COUNT_SEVERE 严重 =========="
if [ "$SG_COUNT_SEVERE" -gt 0 ] || [ "$SG_COUNT_WARN" -gt 0 ]; then
    echo "存在问题但可继续（告警不阻断）；严重项会导致启动失败，请优先处理"
    exit 1
fi
echo "全部通过"
exit 0
