# 多用户运行与上线

后端 Python 3.11/FastAPI/SQLAlchemy/Alembic，数据库 PostgreSQL 17，前端 React 19/TypeScript/Vite/React Router/TanStack Query/PDF.js，后台独立 PostgreSQL worker。向量按账号、索引版本保存并分批用 NumPy 余弦检索；认证 Argon2id 和服务端 Cookie；配置密钥 AES-GCM 加密。生产使用 React 静态构建、Nginx 和 Caddy HTTPS。

原版入口/脚本/data 保留。多人版本使用 `compose.multiuser.yaml` 的独立命名卷和 18770 端口，不能把原 SQLite 目录挂载为多人存储。测试用 55433 专用 PG 和随机 schema，Docker演练项目及端口与开发环境分开。

## 首次准备

安装 Docker Desktop（Windows WSL2）或 Docker Engine/Compose（Linux）、Python 3.11。Docker会在镜像内安装前后端依赖；本机Node 22+和 `frontend/node_modules` 用于修改前端及浏览器测试。已有 `.venv` 可继续使用。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-lock.txt
.\.venv\Scripts\python.exe -m pip install pytest
# 首次需要网络下载 PostgreSQL 基础镜像；日常启动不会自动拉取
docker pull postgres:17-alpine
.\prepare-multiuser.ps1
.\start-multiuser.ps1
.\.venv\Scripts\python.exe scripts/multiuser_runtime.py admin
```

Windows 若 PowerShell 的执行策略阻止 `.ps1`，可以执行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare-multiuser.ps1`；日常启动/关闭同样替换脚本名，这只影响当前脚本进程。

Linux 将虚拟环境 Python 换为 `.venv/bin/python`，执行 `bash prepare-multiuser.sh`、`bash start-multiuser.sh`，再运行 `.venv/bin/python scripts/multiuser_runtime.py admin` 交互创建管理员。打开 `http://127.0.0.1:18770`。管理员初次改密后在平台管理创建用户和配置两类平台能力。首次运行需要初始化管理员，没有固定默认密码；当前本机已经创建管理员，原账号保持不变。测试账号不能用于正式环境。

准备脚本生成一次固定数据库密码和 32 字节主密钥，仅存 `.runtime/multiuser.env`，不打印凭据；已存在配置不覆盖。该文件须独立备份且限制运维读取。首次构建下载依赖，当前网络 Docker Hub 不通时可以使用可信镜像源并核对 digest，不能通过修改系统代理/DNS来修复。

## 日常启停

```powershell
.\start-multiuser.ps1
.\stop-multiuser.ps1
```

Linux 使用 `bash start-multiuser.sh`、`bash stop-multiuser.sh`。日常启动只检查既有镜像、运行显式迁移任务并等待健康，不构建、不拉取、不下载模型、不重建主密钥。关闭只停止指定 Compose 项目，保留数据卷；不使用 `down -v`，不结束浏览器、WPS、代理或其他项目。缺依赖时明确失败并要求先准备。

## 用户操作

1. 可用邮箱验证码自行注册，也可由管理员创建账号。管理员创建的临时账号首次登录改密码；邮箱注册用户使用自行设置的密码。邮件注册配置与本地测试见 [邮箱注册说明](EMAIL_REGISTRATION.md)。
2. 数据与设置中，分别通过“回答模型来源”和“向量模型来源”选择“平台提供”或“我的 API”，再选择具体模型，选择后自动保存；页面显示当前已保存的来源和模型。只切换来源用于浏览，选定模型后才改变默认配置；也可在配置列表点击“设为默认回答模型/向量模型”。
3. 个人 API 仅 OpenAI 兼容 HTTPS 地址（443），配置名称、用途、模型和 Key，测试后选择。服务器内网/回环地址被拒绝。
4. 平台可用外部 API 或管理员许可的 Ollama。CPU/16GB 演示建议 qwen2.5:3b 回答、qwen3-embedding:0.6b 向量；模型须事先安装，应用日常启动不下载权重。服务器 Ollama只内网访问。
5. 新建知识库、上传文字 PDF/DOCX/MD/TXT，等待处理和索引；问答中选择模型与资料范围，核对来源。
6. 数据与设置同时展示本人平台调用总额度、剩余量、已预留/完成/未知调用及未完成任务数。未知调用保留额度，避免超时被误算成未计费；个人 API 不消耗平台调用额度。
7. 回答切换保留索引；已有知识库更换向量能力需确认影响后重建。不同向量空间不能一次混检，需缩小范围或统一重建。
8. 数据与设置导出本人资料。管理员备份、恢复、旧数据迁移参见 `BACKUP_MIGRATION.md`。

问题/引用片段发往所选回答服务，文本/问题发往所选向量服务。模型失败不自动切换服务。平台调用额度计数不是第三方账单；未知外部调用保留 unknown，不能宣称超时即退款。

## 域名与服务器

域名和服务器分别准备。把域名 A/AAAA 解析到服务器公网地址，开放入口 80/443；域名备案/地区接入要求按实际服务商规则办理。数据库/API/worker/Ollama不公开端口。

在服务器 `.runtime/multiuser.env` 配置：

```dotenv
DOMAIN=你的域名
MULTIUSER_HOSTS=你的域名,127.0.0.1
MULTIUSER_ORIGINS=https://你的域名
MULTIUSER_SECURE_COOKIE=true
MULTIUSER_PLATFORM_HOSTS=ollama
```

保持已生成主密钥/密码，添加可信域名，不配置任意 Host。准备 `caddy:2.10.2-alpine` 镜像后，执行 `bash start-multiuser.sh --production`。启动前检查生产域名、HTTPS Origin 与 Secure Cookie，缺失则明确失败。Caddy 自动获取公开可信证书，Nginx去除浏览器传入转发头、应用 `--no-proxy-headers`；Cookie Secure/Strict、CSRF和 Origin校验维持。

首次上线和每次升级：先备份、停止 worker、保留原镜像/配置，构建新版，再启动显式迁移；检查 TLS证书、登录、上传、问答、SSE、两个账号隔离及备份。失败停止新版并恢复一致备份/原镜像；不要将新数据库直接降级给旧代码。当前未提供服务器、域名或真实 API 专用凭据，公网验收仍待执行。

## 管理员恢复与主密钥

忘记用户密码可由管理员重置临时凭据，撤销旧会话并强制改密。所有管理员无法登录时，在受控服务器终端注入 V2 配置，执行 `python scripts/reset-admin-password.py --username 管理员账号`，交互输入临时密码；不开放按用户名直接重置的公共接口。主密钥丢失只能恢复独立保存的密钥；没有密钥无法解密现有 Key，需清除密文并重新输入服务凭据。轮换时停止 API/worker，注入旧配置，执行 `python scripts/rotate-master-key.py` 交互输入新固定密钥；事务中重新加密后更新私有配置，保留旧密钥备份，再重启并测试。

## 资源和测试

全站上限在私有 env 中配置 `MULTIUSER_UPLOAD_BYTES`、`MULTIUSER_BATCH_FILES`、`MULTIUSER_STORAGE_BYTES`、`MULTIUSER_DOCUMENTS`、`MULTIUSER_PENDING_JOBS`、`MULTIUSER_GLOBAL_PENDING`、`MULTIUSER_API_CONCURRENCY`、`MULTIUSER_LOCAL_CONCURRENCY`、`MULTIUSER_PLATFORM_CALLS` 和 `MULTIUSER_PARSE_SECONDS`，修改后重建容器配置。管理员页面可覆盖个人存储、文档、排队任务及平台调用额度；能力测试同样使用并发槽和平台调用额度，每账号最多 10 次/分钟。

完整验证：Windows 执行 `.\scripts\verify-multiuser.ps1 -All`；Linux 执行 `bash scripts/verify-multiuser.sh --all`。需要 V1/V2 镜像、测试数据库、已安装测试模型、前端依赖、Edge和Chrome；Linux脚本演练另外需要 `docker.1ms.run/library/docker:29-cli` 测试工具镜像。本地全量入口包含 30 分钟三账号稳定性测试，结果保存于 `.reports`；真实收费 API 和公网环境独立验收。
