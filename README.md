# Local AI Knowledge Workspace

面向学习资料整理的 AI 知识库问答系统。**当前主版本为 V2 多用户版**：每位用户拥有独立的知识库、文档、对话和 API 配置，可以选择平台模型，也可以接入自己的 OpenAI 兼容 API。

系统已完成本地开发和验证，提供服务器部署配置。公网运行需要自行准备服务器、域名及 HTTPS；邮箱真实发信需要配置 SMTP。仓库不附带服务器、收费 API 额度或模型权重。

## 第二版功能

- 账号与私有空间：邮箱验证码注册、邮箱或原用户名登录、退出与改密；资料、检索、对话、下载和任务按账号隔离。
- 文档管理：创建、重命名和删除知识库，批量导入 PDF、DOCX、Markdown、TXT，重复检查、版本替换和原文件预览。
- 解析与索引：可配置切片、Embedding 向量化、余弦相似度检索，处理进度、失败重试及结束任务记录删除。
- 检索增强问答：资料范围选择、SSE 流式回答、停止和重新生成、Markdown 展示、原文引用；文档删除后保留历史引用快照。
- 模型双来源：回答与向量能力分别选择“平台提供”或“我的 API”，选定具体模型自动保存，支持混合来源。
- 管理与运行：用户启停、密码重置、平台模型、能力测试、额度和运行状态；独立 worker、持久化任务、租约、心跳和公平调度。
- 数据管理：个人资料和对话导出、管理员全站备份恢复、旧版资料选择迁移，API Key 使用 AES-GCM 加密保存。

最近更新包含失败问答任务删除、独立模型来源选择入口和邮箱注册。删除任务记录保留文档、对话、引用和已使用额度；已有知识库更换向量模型需要确认重建。

## 技术栈

| 部分 | 技术 |
| --- | --- |
| 前端 | React 19、TypeScript、Vite、React Router、TanStack Query、PDF.js、原生 CSS |
| 后端 | Python 3.11、FastAPI、Pydantic、Uvicorn、SQLAlchemy、Alembic |
| 数据与检索 | PostgreSQL 17、NumPy，按账号和索引版本保存向量，分批余弦检索 |
| 文档解析 | PyMuPDF、python-docx、lxml |
| 模型接入 | Ollama、OpenAI 兼容回答与 Embedding API |
| 认证与配置 | Argon2id、服务端 HttpOnly Cookie、CSRF、AES-GCM |
| 运行与测试 | Docker Compose、独立 worker、Nginx、Caddy、Pytest、Playwright、Edge、Chrome |

当前使用 NumPy 实现向量检索，没有引入独立向量数据库或 Redis。单机兼容实现使用 SQLite，其迁移读取、共用组件和回归测试保留在源码中；下方教程以 V2 为准。

## 运行前准备

- Windows 10/11：Docker Desktop 并启用 WSL 2；Linux：Docker Engine 和 Docker Compose 插件。
- Python 3.11：用于本地准备、启停和运维脚本。
- 首次安装和构建需要联网下载基础镜像及依赖。
- 修改前端或运行浏览器测试时，需要 Node.js 22+、npm 和对应浏览器。
- API 模式需准备服务地址、模型名称及 API Key；平台本地模型需安装 Ollama 并下载模型。

16GB 内存、无独立 GPU 可以运行轻量本地模型，生成速度依模型和资料规模而异。回答和向量都使用本地服务时，首次下载后可离线使用；外部 API 和 SMTP 发信需要联网。

## Windows 首次启动

在 PowerShell 中执行：

```powershell
git clone https://github.com/whisbest/local-ai-knowledge-workspace.git
cd local-ai-knowledge-workspace
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-lock.txt
docker pull postgres:17-alpine
powershell -NoProfile -ExecutionPolicy Bypass -File .\prepare-multiuser.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-multiuser.ps1
.\.venv\Scripts\python.exe scripts/multiuser_runtime.py admin
```

最后一步在终端交互创建管理员，输入自选用户名和密码，密码至少 12 个字符。管理员首次登录需修改密码，然后重新登录。**没有固定默认管理员或测试账号**，本机演示账号和数据库不会随仓库上传。

浏览器打开 <http://127.0.0.1:18770>。准备脚本生成私有 `.runtime/multiuser.env`，保存固定数据库密码和主密钥，并构建前后端镜像；已有配置不覆盖。该文件需独立备份，丢失主密钥会导致无法解密原有 API Key。

日常启动与关闭：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-multiuser.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop-multiuser.ps1
```

日常启动不会自动构建镜像或下载模型，关闭保留数据卷。更新代码后先备份，再运行准备脚本重新构建并启动。不要使用 `docker compose down -v` 清空数据。

## Linux 首次启动

```bash
git clone https://github.com/whisbest/local-ai-knowledge-workspace.git
cd local-ai-knowledge-workspace
python3.11 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-lock.txt
docker pull postgres:17-alpine
bash prepare-multiuser.sh
bash start-multiuser.sh
.venv/bin/python scripts/multiuser_runtime.py admin
```

日常使用 `bash start-multiuser.sh` 和 `bash stop-multiuser.sh`。默认入口绑定本机地址；公网入口与域名设置见 [部署文档](docs/multiuser/DEPLOYMENT.md)。

## 模型配置与使用

### 平台提供模型

管理员进入“平台管理”，分别添加回答和向量能力并测试。平台可配置外部兼容 API，也可使用 Ollama。用户进入“数据与设置”，选择来源“平台提供”，再选择具体可用模型。

CPU 演示推荐安装并启动 Ollama 后下载：

```bash
ollama pull qwen2.5:3b
ollama pull qwen3-embedding:0.6b
```

Windows Docker 访问宿主机 Ollama 时，管理员配置地址为 `http://host.docker.internal:11434`，模型名称分别为 `qwen2.5:3b` 和 `qwen3-embedding:0.6b`。地址须在平台许可列表中；下载完成后仍需保存平台配置并测试，安装 Ollama 不会自动生成平台模型配置。

Linux 使用宿主机 Ollama 时需保证服务能通过许可地址被容器访问；也可自行将 Ollama 放在同一容器网络，通过许可的 `ollama` 主机名连接。不要将 Ollama 直接开放到公网。

### 我的 API

在“数据与设置 → 添加我的 API”填写配置名称、能力、兼容服务地址、模型名称和 API Key，保存后点击“测试能力”。测试成功后选择来源“我的 API”，再选定具体模型；也可在配置列表点击“设为默认”。

个人 API 支持 OpenAI 兼容 HTTPS 地址，端口为 443，不接受内网、回环地址。服务商需提供对应回答或 Embedding 接口。回答和向量分别配置，可用不同服务商，也可与平台模型混用。

切换来源只是浏览配置，选定具体模型后才保存默认设置。没有平台模型时联系管理员配置，没有个人模型时先添加 API；未测试、停用或测试失败的配置不能选用。

### 从资料开始问答

1. 登录并选择默认回答和向量模型。
2. 新建知识库并上传 PDF、DOCX、Markdown 或 TXT，每批最多 20 个、单文件最多 64MB。
3. 在“文档处理”查看状态，失败时检查原因、重试或删除结束的记录。
4. 在“问答对话”选择资料范围，输入问题并点击引用核对原文。
5. 在“数据与设置”导出本人资料，管理员负责全站备份恢复。

默认向量设置用于新知识库；已有知识库更换向量能力需确认后重建。模型失败不会自动切换服务。外部回答服务接收问题、必要上下文和检索片段；外部向量服务接收待向量化文本。

## 邮箱注册和服务器部署

首次准备默认启用本地验证码预览：注册页面不会发送真实邮件，管理员在“平台管理 → 本地验证邮件”中查看验证码，注册者填写验证码完成注册。管理员与注册者请使用不同浏览器或无痕窗口，避免共享会话。

服务器部署时在私有 `.runtime/multiuser.env` 配置 SMTP 发件邮箱、授权码、TLS 和发件地址。真实 SMTP 需按自己的服务商验证送达；公网需要服务器、域名解析和 HTTPS，不能使用本地验证码预览。具体步骤见 [邮箱注册与 SMTP](docs/multiuser/EMAIL_REGISTRATION.md) 和 [部署说明](docs/multiuser/DEPLOYMENT.md)。

数据库、API、worker 和 Ollama 不直接公开端口。生产提供 Caddy HTTPS 入口，要求可信 Host、HTTPS Origin 和 Secure Cookie。域名和服务器需分别准备，提供部署配置不代表系统已经公网运行。

## 数据和上传范围

V2 数据保存在服务端 Docker 命名卷：`multiuser_pg` 保存 PostgreSQL，`multiuser_files` 保存原文件和业务文件，实际卷名随 Compose 项目前缀变化。刷新、关闭或重建容器后保留数据。备份、主密钥保护和单机资料迁移见 [备份与迁移](docs/multiuser/BACKUP_MIGRATION.md)。

仓库只包含源码、依赖清单、配置模板、测试样例和文档，**不包含**：

- 简历、论文等个人文件、真实上传资料、数据库及备份。
- API Key、SMTP 授权码、真实 `.env`、`.runtime` 配置及账号数据。
- Ollama 模型权重、Docker 镜像、容器数据及虚拟磁盘。
- Python 虚拟环境、Node 依赖、构建产物、缓存及测试报告。

Dockerfile、Compose YAML、Nginx/Caddy 配置和启停脚本是可复现运行需要的小型文本文件，保留在仓库。镜像、依赖和模型按本教程自行构建或下载。

## 开发与测试

后端依赖锁定于 `backend/requirements-lock.txt`，前端依赖锁定于 `frontend/package-lock.json`。

```powershell
cd frontend
npm ci
$env:VITE_MULTIUSER='true'
npm run build
Remove-Item Env:VITE_MULTIUSER
cd ..
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe scripts/prepare-multiuser-test.py
.\.venv\Scripts\python.exe -m pytest tests --junitxml=.reports/all-tests.xml
cd frontend
npx playwright test --config playwright.multiuser.config.ts
```

V2 构建需设置 `VITE_MULTIUSER=true`，Dockerfile 自动设置；Linux 手动构建使用 `VITE_MULTIUSER=true npm run build`。测试使用独立 PostgreSQL 服务和临时数据。完整测试还要求 Docker、测试镜像、Ollama 模型及 Edge/Chrome 已准备，详细前置条件见部署文档。

完整验证入口为 Windows 的 `scripts/verify-multiuser.ps1 -All` 或 Linux 的 `bash scripts/verify-multiuser.sh --all`，包括单机/多人回归、检索性能和 30 分钟稳定性验证。

已有记录包括全部后端 135 项通过、模型来源选择浏览器回归和真实本地 Ollama 问答。具体日期、范围和环境见 [测试记录](docs/multiuser/TEST_RESULTS.md)，这些结果不等于真实外部 API、真实 SMTP 或公网已验收。

## 项目结构和文档

```text
backend/app/multiuser/        多用户认证、资料、模型、任务与数据管理
backend/migrations_multiuser/ PostgreSQL 数据库迁移
frontend/src/                页面、模型来源选择、问答与管理界面
scripts/                     准备、初始化、备份恢复、迁移和验证
deploy/                      生产 HTTPS 配置
docs/multiuser/              API、架构、部署、验收与测试文档
tests/                       接口、浏览器、故障、性能和部署测试
compose.multiuser.yaml       第二版 Docker Compose 配置
start-multiuser.*            第二版启动脚本
stop-multiuser.*             第二版关闭脚本
```

[系统架构](docs/multiuser/ARCHITECTURE.md) · [API 文档](docs/multiuser/API.md) · [验收标准](docs/multiuser/ACCEPTANCE.md) · [需求更新](产品需求更新文档.md) · [开发计划](多用户版本开发计划.md)

暂不包含 OCR、图片/音视频知识库、团队共享、移动端专项适配及模型训练。扫描 PDF 需要先获得可提取文字的版本。原单机使用说明见 [旧版安装说明](docs/SETUP_WINDOWS.md)，旧数据目录不能直接挂载到 V2。
