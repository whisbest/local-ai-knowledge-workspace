# 备份、恢复与原版迁移

普通用户在数据与设置导出本人资料、对话和引用，下载需登录本人账号，有效期 7 天。不包含账号凭据、API Key、平台设置、向量索引；个人恢复首版不开放。导出成功后仍建议自行另存文件。

全站备份只在管理员运维终端运行，包含账号哈希、会话哈希、加密模型配置、数据库和文件。备份属于私有敏感运维文件，不能提交 Git；主密钥独立保存，未包含于备份，丢失后无法解密配置。设置固定主密钥，不在日常启动时重新生成。

```powershell
# 环境配置为 V2，先停止 worker 或等待任务结束。运行中任务会阻止维护。
.\.venv\Scripts\python.exe scripts/backup-platform.py --destination .runtime/private-backups/manual.zip

# 预检：校验 manifest、所有文件 SHA256、版本、主密钥、空间、真实 PG 关联约束
.\.venv\Scripts\python.exe scripts/restore-platform.py --archive .runtime/private-backups/manual.zip

# API/worker 停止后，以预检输出的校验和明确确认恢复
.\.venv\Scripts\python.exe scripts/restore-platform.py --archive .runtime/private-backups/manual.zip --confirm-sha256 <预检校验和>
```

维护通过 PostgreSQL 跨进程独占锁阻止 API/worker 事务写入；恢复前另做安全备份，数据库事务失败回滚、文件组还原。恢复交换日志与数据库 commit 标记用于崩溃后恢复，启动检测异常时明确报错，保留 rollback/stage 文件供管理员检查。原文件回退目录不自动删除；备份保留周期由管理员按磁盘容量制定。

Docker 用户可直接使用镜像内置运维入口，不需要把数据库地址或主密钥复制到宿主机环境。下面命令使用默认多人项目；生产环境额外添加 `-f compose.prod.yaml`，自定义项目额外添加相同的 `-p 项目名`：

```powershell
$composeArgs = @('compose','--env-file','.runtime/multiuser.env','-f','compose.multiuser.yaml')
# 停止写入方，保留数据库运行
docker @composeArgs stop api worker web
docker @composeArgs up -d --no-build --pull never --wait db
docker @composeArgs run --rm --no-deps api python -m app.multiuser.operator_entry backup --destination /workspace-data/platform-backups/manual.zip
docker @composeArgs run --rm --no-deps api python -m app.multiuser.operator_entry restore --archive /workspace-data/platform-backups/manual.zip
# 预检后再执行，替换 SHA256；恢复前自动保留安全备份
docker @composeArgs run --rm --no-deps api python -m app.multiuser.operator_entry restore --archive /workspace-data/platform-backups/manual.zip --confirm-sha256 <预检校验和>
.\start-multiuser.ps1
```

备份写入持久化文件卷，应通过 `docker compose cp` 另存到受保护的外部位置；同一磁盘上的副本不能防止磁盘损坏。Linux 同样使用这些 Compose 子命令。已经在真实 Linux 容器、真实 PostgreSQL和文件卷中完成备份、预检及恢复演练。

## 原版选择性迁移

原 `data` 不自动迁移，更不会上传到公网。先保留 `scripts/backup-single-user.py` 一致性备份，然后确定归属用户 ID 和具体文档 ID。只读 SQLite 的所有操作不会改变原数据。

```powershell
# 默认 dry-run，不写文件、不改目标数据
.\.venv\Scripts\python.exe scripts/migrate-single-user.py --source data --owner <目标用户ID> --document <旧文档ID>

# 确认所选范围之后 apply；重复签名能检测，不重复插入
.\.venv\Scripts\python.exe scripts/migrate-single-user.py --source data --owner <目标用户ID> --document <旧文档ID> --apply

# 如需对话，显式添加 --include-conversations；引用快照保留，未选原文标记不可访问
# 尚未产生新任务时可按批次回滚
.\.venv\Scripts\python.exe scripts/migrate-single-user.py --owner <目标用户ID> --rollback-batch <批次ID>
```

迁移重分配 ID 并验证同用户父子关系、数量、文件校验；旧偏好转换为个人偏好，默认模型清空。密钥不迁移，Ollama 地址不导入成个人 API；需要管理员新建平台能力或用户重新填写个人 API。索引一律标记待重建，避免模型/维度/版本变化造成错误检索。原始来源片段历史保留；旧原文件地址不进入输出。生产迁移按用户选择执行，当前演练只使用测试资料。
