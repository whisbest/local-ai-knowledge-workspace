# 个人本地 AI 知识库

五个页面：真实首页概览、知识库管理、文档处理与索引、带来源的持续问答、数据与设置。支持 PDF/DOCX/Markdown/TXT。回答模型与向量模型分别支持本地 Ollama 和兼容 API。

## 现在启动

本机已经安装 Docker Desktop、Ollama，下载 CPU 模型并构建镜像。双击项目目录的 **start.cmd**；浏览器访问 <http://127.0.0.1:18765>。关闭时双击 **stop.cmd**。这些脚本保留 `data/`，每天启动不拉镜像、不下载模型、不构建。

首次进入设置页，添加回答模型 `qwen2.5:3b`、向量模型 `qwen3-embedding:0.6b`，本地地址 `http://host.docker.internal:11434`。分别测试并选择默认，再新建知识库上传自己的资料。正式数据不带演示假资料。

## 第一次准备另一台电脑

安装 Windows Docker Desktop（WSL 2）与 Ollama，确保镜像和权重下载完成。在项目执行 `docker compose build`，Ollama 下载推荐模型。可在设置页确认联网后下载模型。本机 AppData 的 EFS 特殊问题通过 Docker 进程专用 `.runtime` 配置解决，启动脚本自动复用；没有改变系统环境变量。

全本地模式可离线使用。任一模型为 API 模式时，该能力调用需要联网并向自选服务发送文字。禁止自动云端回退。API 协议已做契约测试，真实供应商密钥没有提供，真实兼容性尚待验证。

## 文档与验证

- [Windows 操作说明](docs/SETUP_WINDOWS.md)
- [模型安装配置](docs/MODEL_SETUP.md)
- [数据与备份](docs/DATA_AND_BACKUP.md)
- [验收记录](docs/ACCEPTANCE.md)
- [演示流程](docs/DEMO.md)

后端全量测试：`.venv/Scripts/python.exe -m pytest --junitxml=.reports/all-tests.xml`。浏览器测试：在 frontend 执行 `npx playwright test`；测试使用独立临时数据、协议响应服务和本机 Edge，不污染正式数据。

环境测试依赖真实 Docker 和已下载 Ollama 模型；Docker 离线测试还需要先构建 `docker build -f tests/OfflineGuard.Dockerfile -t lkq-offline-guard:v1 tests`。这个镜像只用于测试期间隔离 api 容器外网，不参与日常运行。

## 限制

个人规模、单任务、CPU 推理；64MB/文件、20文件/批、500万提取字符、3000 PDF 页、4000问题字符。扫描文件没有 OCR。DOCX 展示提取内容而非 Word 排版。索引采用 SQLite + NumPy 分批余弦检索。模型回答仍可能误判证据，务必核对引用。默认备份不含密钥/模型权重，恢复后需要补充。电脑物理重启尚未实际执行，容器重建与进程重启已自动验证。
