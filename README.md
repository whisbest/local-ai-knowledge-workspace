# Local AI Knowledge Workspace

一个运行在个人电脑上的本地 AI 知识库问答系统。用户可以导入 PDF、DOCX、Markdown 和 TXT 资料，经过解析、切片、向量索引后进行问答，并查看回答对应的原文来源。

项目面向个人学习和资料整理场景，数据默认保存在本机，不需要登录、云数据库或远程部署。

## 功能概览

- 知识库管理：创建、重命名、删除知识库，批量导入资料，处理重复文件。
- 文档处理：解析文档、切片、查看处理阶段、失败重试和任务记录删除。
- 向量检索：使用本地 SQLite 与 NumPy 保存向量并进行归一化余弦检索。
- AI 问答：支持连续对话、问题范围选择、停止生成、重新生成和复制回答。
- 来源引用：回答中的 `[1]` 等引用可以定位到原始片段；文档删除后仍保留历史引用快照。
- Markdown 回答：标题、列表、加粗、引用、代码和引用编号会以可读格式展示。
- 双模型模式：回答模型和向量模型分别支持本地 Ollama 或 OpenAI 兼容 API。
- 数据管理：本地数据统计、备份、校验恢复、偏好设置和模型配置管理。
- Docker 运行：提供 Windows 启动和关闭脚本，数据通过本地目录持久化。

## 技术栈

### 前端

- React 19、TypeScript、Vite
- React Router 7
- TanStack Query 5
- PDF.js：本地 PDF 页面预览
- 原生 CSS：桌面端现代 AI 工具风格界面，不依赖 CDN 和在线字体

### 后端

- Python 3.11
- FastAPI、Pydantic 2、Uvicorn
- SQLAlchemy 2、Alembic
- SQLite WAL：保存业务记录、任务、向量和流式事件
- NumPy：批量归一化向量和余弦相似度检索
- PyMuPDF、python-docx、lxml：解析 PDF、DOCX 和文字资料

### 运行与测试

- Docker Desktop、Docker Compose、WSL 2
- Ollama：可选的本地回答和向量模型服务
- Playwright + Microsoft Edge：浏览器端到端测试
- Pytest：后端单元、接口、故障和持久化测试

## 目录结构

```text
backend/       FastAPI 服务、业务模块和数据库迁移
frontend/      React 前端、页面、样式和浏览器测试配置
docs/          页面规格、模型配置、备份、验收和演示文档
tests/         后端测试、端到端测试和测试样例文件
compose.yaml   Docker Compose 服务定义
start.cmd      Windows 启动脚本
stop.cmd       Windows 关闭脚本
```

## 快速启动（Windows）

### 1. 准备环境

推荐环境：

- Windows 10/11
- Docker Desktop，启用 WSL 2
- 16GB 内存、无独立 GPU 也可以运行轻量量化模型
- 如果使用本地模型，安装 Ollama；如果只使用 API 模式，可以不安装 Ollama

### 2. 首次构建

在项目根目录执行：

```powershell
Copy-Item .env.example .env
docker compose build
```

首次构建需要联网下载 Docker 基础镜像、Python 依赖和前端依赖。日常启动脚本不会自动拉取镜像、下载模型或重新构建。

### 3. 启动和关闭

双击 `start.cmd`，或执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

浏览器访问：<http://127.0.0.1:18765>

关闭应用：

```powershell
powershell -ExecutionPolicy Bypass -File .\stop.ps1
```

关闭只会停止本项目容器，不会删除 `data/`，也不会关闭用户已有的 Ollama 服务。

### 4. 配置模型

进入“数据与设置”：

1. 添加回答模型配置。
2. 添加向量模型配置。
3. 分别测试模型能力。
4. 分别选择默认回答模型和默认向量模型。
5. 新建知识库并上传资料。

本地 Ollama 推荐配置：

```text
回答模型：qwen2.5:3b
向量模型：qwen3-embedding:0.6b
服务地址：http://host.docker.internal:11434
```

首次下载本地模型可以联网，下载完成且两个模型都使用本地模式后，日常问答可以完全离线：

```powershell
ollama pull qwen2.5:3b
ollama pull qwen3-embedding:0.6b
```

也可以分别接入 OpenAI 兼容 API。API 模式下，问题、对话上下文和检索片段可能发送给回答服务；向量 API 还会接收待向量化的文本。系统不会在本地模式失败时自动切换到 API。

## 开发环境

如果需要脱离 Docker 修改和调试代码：

- Python 3.11，并安装 `backend/requirements-lock.txt`
- Node.js 22，并在 `frontend/` 执行 `npm ci`

前端开发服务器：

```powershell
cd frontend
npm run dev
```

后端开发服务器需要设置 `DATA_ROOT`，并使用 Uvicorn 工厂模式启动：

```powershell
$env:DATA_ROOT = "..\data-dev"
..\.venv\Scripts\python.exe -m uvicorn app.main:application --factory --app-dir ..\backend --reload
```

## 测试与构建

后端全量测试：

```powershell
.venv\Scripts\python.exe -m pytest --junitxml=.reports/all-tests.xml
```

前端生产构建：

```powershell
cd frontend
npm run build
```

浏览器端到端测试：

```powershell
cd frontend
npx playwright test
```

测试使用独立临时数据和协议模拟服务，不读取正式 `data/`。Docker 离线测试使用的 `OfflineGuard` 镜像只用于测试，不属于日常运行依赖。

## 数据、备份与隐私

- 业务数据默认保存在项目根目录的 `data/`，包括 SQLite、原文件、解析结果、索引、对话和备份。
- API Key 单独保存在 `data/secrets/`，不会写入普通设置或默认备份。
- Ollama 模型权重由 Ollama 管理，不进入 Git 仓库，也不进入应用备份。
- 使用“数据与设置”页面创建完整备份；恢复前会自动创建恢复前备份并校验压缩包。
- 不要把真实资料、API Key、`.env` 或 `data/` 上传到 GitHub。


## 相关文档

- [Windows 安装与启动](docs/SETUP_WINDOWS.md)
- [模型安装与双模式配置](docs/MODEL_SETUP.md)
- [本地数据、备份与恢复](docs/DATA_AND_BACKUP.md)
- [页面规格](docs/PAGE_SPEC.md)
- [验收标准](docs/ACCEPTANCE.md)
- [演示流程](docs/DEMO.md)
- [测试结果](docs/TEST_RESULTS.md)

## 已知限制

这是面向个人规模的本地系统：单个文件最大 64MB，每批最多 20 个文件，问题最大 4000 字符。当前不包含 OCR、移动端、多用户、云同步和模型训练。模型回答仍可能存在误判，应结合来源片段核对。
