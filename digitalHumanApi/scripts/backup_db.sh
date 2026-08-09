#!/usr/bin/env bash
# =============================================================================
# backup_db.sh — SQLite 数据库在线安全备份脚本（批次8 C7 数据库备份策略）
#
# 为什么用 sqlite3 .backup 而不是文件复制？
#   - `.backup` 走 SQLite 在线备份 API（与 VACUUM INTO 同族）：备份期间源库
#     仍可被业务并发读写（不中断服务），产物内部一致（快照语义）
#   - 裸 `cp` 在并发写场景可能拷到「写到一半」的库文件（journal/WAL 未合并），
#     静默损坏且难以发现；.backup 则天然安全
#   详见 doc/database-backup.md（备份/恢复/验证/Docker volume 路径说明）
#
# 特性：
#   - 备份文件名带时间戳：<库名>_backup_YYYYmmdd_HHMMSS.db（字典序=时间序，
#     便于按名排序清理旧份）
#   - 保留最近 N 份（默认 7），超量自动清理最旧
#   - 备份目录可配：本地默认 digitalHumanApi/backups；Docker 部署把数据库
#     路径与备份目录作为位置参数传入（volume 挂载点，见 doc/database-backup.md）
#   - 先写临时文件再原子改名，失败不残留损坏产物
#   - 备份后自动校验（可恢复性冒烟）：PRAGMA integrity_check + 表数量与源库对比
#
# 用法（位置参数 > 环境变量 > 默认值）：
#   scripts/backup_db.sh [数据库路径] [备份目录] [保留份数]
#   DB_PATH=... BACKUP_DIR=... BACKUP_KEEP=... scripts/backup_db.sh
#   # Docker 示例（volume 挂载 /app 时）：
#   scripts/backup_db.sh /app/digital_human.db /app/backups 14
#
# 依赖：sqlite3 CLI（Debian/Ubuntu: apt-get install sqlite3；Dockerfile 需
#   apt-get 安装后可用，或宿主机 crontab 直接调度）
# =============================================================================
set -euo pipefail

# --- 参数解析（位置参数优先，其次环境变量，最后默认值） ----------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="${1:-${DB_PATH:-$SCRIPT_DIR/../digital_human.db}}"
BACKUP_DIR="${2:-${BACKUP_DIR:-$SCRIPT_DIR/../backups}}"
KEEP="${3:-${BACKUP_KEEP:-7}}"

# --- 前置检查 ----------------------------------------------------------------
if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "错误: 未找到 sqlite3 命令，请先安装（Debian/Ubuntu: sudo apt-get install sqlite3）" >&2
    exit 1
fi
if [ ! -f "$DB_PATH" ]; then
    echo "错误: 数据库文件不存在: $DB_PATH" >&2
    exit 1
fi
if ! [[ "$KEEP" =~ ^[0-9]+$ ]] || [ "$KEEP" -lt 1 ]; then
    echo "错误: 保留份数必须为正整数，当前值: $KEEP" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# --- 备份文件命名（时间戳，字典序=时间序，便于按名清理） ----------------------
db_basename="$(basename "$DB_PATH")"
db_stem="${db_basename%.db}"                        # digital_human.db -> digital_human
backup_pattern="${db_stem}_backup_*.db"
backup_name="${db_stem}_backup_$(date +%Y%m%d_%H%M%S).db"
# 同一秒内重复备份（如测试/手动连续执行）不覆盖：追加 -2/-3 后缀
suffix=1
while [ -e "$BACKUP_DIR/$backup_name" ]; do
    suffix=$((suffix + 1))
    backup_name="${db_stem}_backup_$(date +%Y%m%d_%H%M%S)-${suffix}.db"
done
backup_path="$BACKUP_DIR/$backup_name"
temp_path="$BACKUP_DIR/.$backup_name.tmp"           # 临时文件，成功后原子改名

# 脚本退出时清理临时文件（成功路径 mv 后已不存在，rm -f 安全）
trap 'rm -f "$temp_path"' EXIT

# --- 在线安全备份（sqlite3 .backup） ------------------------------------------
# 先写临时文件再 mv（原子替换）：.backup 中断时不会在备份目录留下半成品
sqlite3 "$DB_PATH" ".backup '$temp_path'"
mv "$temp_path" "$backup_path"

# --- 备份可恢复性校验（临时库打开 + 表计数，见 doc/database-backup.md）--------
integrity="$(sqlite3 "$backup_path" "PRAGMA integrity_check;")"
if [ "$integrity" != "ok" ]; then
    echo "错误: 备份完整性校验失败（integrity_check=$integrity），已删除该备份" >&2
    rm -f "$backup_path"
    exit 1
fi
source_tables="$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")"
backup_tables="$(sqlite3 "$backup_path" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';")"
if [ "$source_tables" != "$backup_tables" ]; then
    echo "错误: 备份表数量($backup_tables)与源库($source_tables)不一致，已删除该备份" >&2
    rm -f "$backup_path"
    exit 1
fi

# --- 清理旧备份（按修改时间排序，保留最近 KEEP 份） ---------------------------
# 按 mtime 而非文件名排序：时间戳冲突后缀（-2/-3）不影响“保留最新”语义
mapfile -t backups < <(
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "$backup_pattern" \
        -printf '%T@ %p\n' | sort -n | cut -d' ' -f2-
)
while [ "${#backups[@]}" -gt "$KEEP" ]; do
    rm -f "${backups[0]}"
    backups=("${backups[@]:1}")
done

echo "备份完成: $backup_path"
echo "校验通过: integrity_check=$integrity 表数量=$backup_tables（源库 $source_tables）| 当前保留 ${#backups[@]} 份（上限 $KEEP）"
