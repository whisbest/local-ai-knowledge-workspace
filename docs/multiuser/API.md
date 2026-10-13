# V2 接口约定

同源 `/api`；除 `/health`、`/auth/login`、`/auth/options`、`/auth/registration-code`、`/auth/register` 外所有接口要求服务端会话。写请求传登录/`auth/me` 返回的 `csrf` 到 `X-CSRF-Token`。会话凭据仅 HttpOnly Cookie；不存入浏览器 localStorage。跨用户 ID 返回 404；普通用户访问 admin 返回 403。参数错误不回显提交值。

- 注册：GET `auth/options`；POST `auth/registration-code`（email）；POST `auth/register`（email/code/password）。验证码10分钟、60秒重发冷却、最多5次校验、邮件发送/确认限流；公开注册创建普通用户，不能指定角色。POST登录的username字段兼容邮箱和旧用户名。GET `admin/registration-mail`仅在本地测试模式给管理员预览验证码，SMTP模式404。
- 账号：POST `auth/login`（username/password）、GET `auth/me`、POST `auth/logout`、POST `auth/password`（current_password/new_password，撤销全部会话）。首次和重置后先改密码。
- 知识库：GET/POST `knowledge-bases`；PATCH/DELETE `knowledge-bases/{id}`；GET `impact`；GET `embedding-impact`；PUT `embedding`（embedding_id/profile/confirm），确认重建全部资料。
- 文档：POST `documents/upload` multipart（kb_id/request_key/decision/files），GET `documents`（kbId/q/status/format/page/page_size），GET `documents/{id}` 及 `/file`、`/parsed`、`/chunks`、`/versions`；POST `/reprocess`，DELETE 文档。查重范围为本人目标知识库。
- 任务：GET `jobs`；POST `jobs/{id}/cancel` 和 `/retry`；DELETE 终态任务。队列由独立 worker 领取。
- 检索：POST `retrieval`（question/scope/top_k）；GET `indexes/impact`；POST `indexes/rebuild` 和 `/clear`。scope 为 all/kb/documents，后两种包含 ids，越权混合批次整体拒绝。
- 对话：GET/POST `conversations`；PATCH/DELETE `conversations/{id}`；GET `/messages`；POST `/generations`（question/model_id/scope/request_key）。GET `generations/{id}`、`/sources`、`/events?after=序号`；POST `/stop`、`/regenerate`。SSE 支持 Last-Event-ID，每次轮询重验会话。
- 模型：GET/POST `model-configs`，POST `model-configs/{id}/test`，PATCH name/key，DELETE 未使用配置。平台模型只有管理员可测试、修改；公开能力不含内网地址/密钥。服务地址/模型/能力变更新增配置版本，通过知识库重绑。
- 设置：GET/PUT `settings`；GET `dashboard` 和 `data/overview`（本人文档/存储/向量/任务，以及 platform_calls、platform_call_limit、platform_calls_remaining、预留/完成/未知调用和 pending_jobs/pending_job_limit）；POST `data/clear`（scope/confirm），只清理本人。
- 管理员：GET/POST `admin/users`，PATCH 用户 enabled/password/quota；GET/POST `admin/models`，PATCH 模型 enabled/name/key，POST `/test`，GET `/impact`。运营接口不返回私人正文。

- 导出：POST `exports` 创建本人 ZIP；GET `exports` 列表；GET `exports/{id}/file` 授权下载，7 天有效，排除模型密钥、账号凭据及向量。个人恢复未开放。
- 运维：镜像内 `python -m app.multiuser.operator_entry backup/restore`；宿主机提供备份/恢复、只读单机迁移、管理员初始化、密码重置和密钥轮换脚本。操作步骤见 `BACKUP_MIGRATION.md` 和 `DEPLOYMENT.md`。

不存在公开的旧 SQLite 覆盖恢复路由。模型能力测试限制每账号 10 次/分钟，平台测试计入操作者调用额度，失败外部调用保留未知状态；普通用户不能借测试接口调用平台模型。
