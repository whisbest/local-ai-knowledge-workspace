# Windows 运行基础

Docker Desktop 使用本项目 `.runtime/roaming` 和 `.runtime/local` 保存运行配置，避开本机 AppData 的 EFS 兼容问题。应用数据保存于 `data/`。

首次构建：`docker compose build`。
启动：`docker compose up -d --no-build --wait`。
浏览器：<http://127.0.0.1:18765>。
停止：`docker compose down`，保留全部数据。最终交付会提供双击脚本。

阶段 1 检查：4 项测试通过，包含真实 Compose 容器重建。报告 `.reports/stage1.xml`。
