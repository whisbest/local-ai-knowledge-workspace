# Windows 启动与关闭

Docker Desktop 使用本项目 `.runtime/roaming` 和 `.runtime/local` 保存运行配置，避开本机 AppData 的 EFS 兼容问题。应用数据保存于 `data/`。

首次构建：`docker compose build`。
日常启动：双击 `start.cmd`，或执行 `powershell -NoProfile -ExecutionPolicy Bypass -File ./start.ps1`。自动等待 Docker，检查本机镜像，不执行联网拉取或构建。Ollama 已启动则复用，未启动且已安装则后台启动。
浏览器：<http://127.0.0.1:18765>。
日常停止：双击 `stop.cmd`，或执行 `powershell -NoProfile -ExecutionPolicy Bypass -File ./stop.ps1`。停止并移除应用容器、保留绑定目录；Docker Desktop 和既有 Ollama 服务供其他程序继续使用。

`stop.ps1 -StopOwnedOllama` 仅关闭本项目启动并记录进程号的 Ollama 进程；不会关闭用户原有模型服务。关闭电脑或浏览器不删除应用数据。

`.env.example` 可复制为 `.env`，APP_PORT 修改本机端口，DATA_PATH 可填本机绝对路径。已有资料时变更路径不会自动迁移，需使用备份恢复。程序仅对主机发布 127.0.0.1 的 web 端口。

PowerShell 启动 `-NoBrowser` 可用于测试或后台运行。默认启动会打开浏览器。脚本错误会明确报告，不删除数据；双击窗口失败时保留供查看。

更新代码后先完成构建与测试，再执行启动脚本重新创建应用容器。不要把删除 data、删除数据卷或 Docker 清理命令当成日常关闭。

阶段 1 检查：4 项测试通过，包含真实 Compose 容器重建。报告 `.reports/stage1.xml`。
