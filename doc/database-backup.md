# 数据库备份策略（批次8 C7）

SQLite 备份方法对比、备份脚本使用、恢复验证、Docker 部署下的 volume 路径说明。
配套脚本：`digitalHumanApi/scripts/backup_db.sh`（shellcheck 干净，无 python
依赖，仅需 sqlite3 CLI）；冒烟测试：`digitalHumanApi/tests/test_backup_script.py`。

## 备份方法：sqlite3 .backup vs 文件复制

| 方法 | 安全性 | 是否在线 | 结论 |
|------|--------|----------|------|
| `sqlite3 <库> ".backup '<目标>'"` | 走 SQLite 在线备份 API（快照语义，产物内部一致） | ✅ 备份期间业务可继续读写 | **推荐**（本脚本默认） |
| `cp` / `rsync` 裸文件复制 | 并发写场景可能拷到「写到一半」的库文件（journal/WAL 未合并），静默损坏 | ❌ 需停写或冒风险 | 不推荐 |
| `VACUUM INTO '<目标>'` | 同 .backup 语义 | ✅ | 等价替代，但需在应用内执行 |

关键点：应用运行中（轮询/对话写库）裸 `cp` 可能得到不一致快照；`.backup`
由 SQLite 自身处理 journal，备份文件始终是自洽的完整数据库。

## 备份脚本 `scripts/backup_db.sh`

用法（位置参数 > 环境变量 > 默认值）：

```bash
# 默认：digitalHumanApi/digital_human.db → digitalHumanApi/backups/，保留 7 份
scripts/backup_db.sh

# 显式参数：数据库路径 / 备份目录 / 保留份数
scripts/backup_db.sh /path/to/digital_human.db /path/to/backups 14

# 环境变量方式（cron 场景）
DB_PATH=... BACKUP_DIR=... BACKUP_KEEP=7 scripts/backup_db.sh
```

行为：

1. 前置检查：sqlite3 CLI 存在、源库存在、`KEEP` 为正整数
2. 在线安全备份：`sqlite3 "$DB_PATH" ".backup"` 写临时文件 → 原子改名
   （失败不残留半成品；同一秒内重复备份自动追加 `-2/-3` 后缀不覆盖）
3. **自动校验（可恢复性冒烟）**：`PRAGMA integrity_check` = ok 且备份表数量
   与源库一致，否则删除该备份并退出非零
4. 清理旧备份：按修改时间排序保留最近 `KEEP` 份

命名：`<库名>_backup_YYYYmmdd_HHMMSS.db`（如 `digital_human_backup_20260810_153000.db`）。

依赖：`sqlite3` CLI（Debian/Ubuntu `sudo apt-get install sqlite3`）。

### 定时备份（cron 示例）

```cron
# 每天 02:30 备份，保留 14 份（≈2 周）
30 2 * * * cd /path/to/ScenicGuide-AI/digitalHumanApi && ./scripts/backup_db.sh >> /var/log/backup_db.log 2>&1
```

## 恢复验证步骤

恢复 = 把备份当作完整数据库打开/使用。手工验证流程：

```bash
# 1. 把备份拷贝为独立库（或直接指向备份文件）
cp backups/digital_human_backup_20260810_153000.db restore_check.db

# 2. 打开临时库验证完整性 + 表数量（应等于业务表数；schema 见 doc/alembic-migrations.md）
sqlite3 restore_check.db "PRAGMA integrity_check;"     # 期望输出: ok
sqlite3 restore_check.db "SELECT COUNT(*) FROM sqlite_master WHERE type='table';"

# 3. 抽查业务数据（如对话/评估/用量表最近行）
sqlite3 restore_check.db "SELECT COUNT(*) FROM conversations;"

# 4. 验证通过后，正式恢复 = 停服 → 备份覆盖源库 → 重启
#    （SQLite 单文件，覆盖 digital_human.db 即可；memory_store.db 同理）
```

脚本本身已内置第 2 步（备份即校验），恢复演练时执行上述步骤即可确认
「备份可恢复」。建议 staging 每次演示前跑一次备份，prod 定期做恢复演练
（不演练的备份 = 没备份）。

## Docker 部署下的 volume 路径说明

`docker-compose.yml` 的 api 服务挂载 `./digitalHumanApi:/app`（容器内工作目录
即 `/app`），因此：

| 文件 | 宿主机路径 | 容器内路径 |
|------|-----------|-----------|
| 主库 | `digitalHumanApi/digital_human.db` | `/app/digital_human.db` |
| 短期记忆库 | `digitalHumanApi/memory_store.db` | `/app/memory_store.db` |
| 备份脚本 | `digitalHumanApi/scripts/backup_db.sh` | `/app/scripts/backup_db.sh` |
| 备份目录（建议） | `digitalHumanApi/backups/` | `/app/backups/` |

**备份脚本路径参数化**（Docker 场景直接传容器内路径，宿主机与容器路径一一
对应，无需进容器）：

```bash
# 宿主机直接跑（脚本在挂载目录内，路径即容器内路径）
./digitalHumanApi/scripts/backup_db.sh /app/digital_human.db /app/backups 14

# 或进容器执行
docker compose exec api /app/scripts/backup_db.sh /app/digital_human.db /app/backups 14
```

备份目录 `backups/` 位于挂载卷内，**宿主机侧直接可见**
（`digitalHumanApi/backups/`），cron 定时备份与手动备份均无需进容器。注意：

- `*.db` 已在 `.gitignore`，备份文件同样不入库
- 生产建议把 `backups/` 目录额外同步到异地/对象存储（本策略只保证本机可恢复）
- 数据库文件为 SQLite 单文件，备份即单文件拷贝，无多文件一致性问题
