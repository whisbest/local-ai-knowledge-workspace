# 执行决策记录

## 阶段 0 环境

- 目标电脑：Windows 11，Intel i5-12450H，16GB 内存，无独立 GPU。
- 安装 Docker Desktop 4.94.0（用户级）、Ollama 0.40.2；WSL 2.6.3。
- 安装包通过发布的 SHA256 校验。原机未安装 Docker 和 Ollama。
- Windows 用户 AppData 目录启用 EFS，Docker 对配置文件和 Unix socket 的重命名失败。未移动或删除原用户目录。为 Docker 子进程指定项目内 `.runtime/roaming`、`.runtime/local`，保留原目录和系统环境；后续启动脚本复用该进程级配置。
- Docker 命令调用时临时补充其 resources/bin 路径，避免凭据辅助程序不在 PATH。
- Docker Hub 当前网络访问失败，基础镜像先经 Daocloud 镜像服务拉取；需记录 RepoDigest，应用仍需通过真实 Docker 检查。
- 回答候选 qwen2.5:3b（Q4_K_M，约 1.9GB）。向量默认候选 qwen3-embedding:0.6b（约 639MB）；bge-m3 仅为初始比较候选，不作为默认常驻模型。
- 模型保持单实例、CPU 推理；测试后立即卸载，避免与 Docker/其他桌面程序争抢内存。
- 真实服务商 API 密钥尚未提供。不得读取用户其他项目密钥或将模拟结果标为真实服务商通过。

测试结果以 `.reports` 和 ACCEPTANCE 中记录为准。阶段 0 尚未通过时不勾选完成，不进入阶段 1。
