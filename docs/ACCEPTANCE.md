# PRD 第一版验收记录

检查日期：2026-10-10；目标 Windows 11 / i5-12450H / 16GB / CPU。定义沿用父目录 PRD.md AC-01～AC-31。

每项操作与预期见 PRD 原文和对应可重复测试，实际结果如下。测试使用公开自制样本与独立临时目录，不污染正式数据。

| 编号 | 状态 | 操作与实际结果 | 可重复证据（tests 或 .reports） |
| --- | --- | --- | --- |
| AC-01 | 通过 | 双击脚本实际启停隔离 Compose；本机网页可达且数据保留 | test_stage8_docker.py / stage8-docker-real.json |
| AC-02 | 通过 | 空数据库 dashboard 全零、无虚构记录；首页导入/模型配置入口 | test_stage1.py,test_stage6.py / 首页实机截图 |
| AC-03 | 通过 | 四类文件+损坏/扫描样本批量导入，独立失败不影响其他文件；模型配置后进入ready | test_stage3.py,test_stage4.py |
| AC-04 | 通过 | 同校验内容提示，跳过无片段增长；替换后旧索引不可检索 | test_stage3.py,test_stage8_combinations.py |
| AC-05 | 通过 | 原文件副本按UUID保存；接口读取内容与样本一致，不依赖用户原路径 | test_stage3.py,test_stage8_docker.py |
| AC-06 | 通过 | PDF两页定位/跨页跨度，TXT/MD行范围、DOCX段落；切片非空 | test_stage3.py / fixtures |
| AC-07 | 通过 | 非法参数拒绝，合法参数重新处理切片数量变化 | test_stage1.py,test_stage3.py |
| AC-08 | 通过 | 任务claim互斥、失效租约中断；重试新attempt保留错误 | test_stage1.py,test_stage3.py |
| AC-09 | 通过 | 首页携带同一问题、选定知识库，只提交一次，刷新不再调用 | e2e/flow.spec.ts 首页流程 |
| AC-10 | 通过 | 首页统计来自真实数据库，可检索统计跳转status=ready | test_stage6.py / e2e 首页统计 |
| AC-11 | 通过 | 冲突A/B知识库、文档范围过滤；第二范围不带上次回答当证据 | test_stage4.py,test_stage8_faults.py |
| AC-12 | 通过 | 真实本地七天答案与[1]；虚假编号回答failed，来源仅实际召回 | test_stage5_real.py,test_stage5.py |
| AC-13 | 通过 | 真实火星人口问题明确资料不足；无片段直接不足不调用模型 | stage5-real.json / test_stage5.py |
| AC-14 | 通过 | 来源卡片与快照包含实际text、来源、相似度，可与引用对应 | test_stage5.py / e2e 来源流程 |
| AC-15 | 通过 | PDF.js真实页渲染与第二页定位；其他格式直接显示来源段落快照 | e2e PDF页码流程 |
| AC-16 | 通过 | 部分delta后停止保留文字、后续不写入；刷新重放不调用；重启interrupted | test_stage5.py / e2e 刷新流程 |
| AC-17 | 通过 | 搜索/重命名/继续及按类删除对话；持久化重启可读 | test_stage5.py,test_stage8_faults.py |
| AC-18 | 通过 | 同名新内容替换从七天变三十天，处理中无旧召回；删除后无召回 | test_stage8_combinations.py,test_stage4.py |
| AC-19 | 通过 | 已删除/替换来源的历史text保持，来源状态明确 | test_stage5.py,test_stage8_combinations.py / e2e |
| AC-20 | 通过 | 真实Ollama回答/1024维向量独立测试；缺模型/服务分别诊断 | test_stage2.py,test_stage8_combinations.py |
| AC-21 | 部分验证 | API双用途契约、鉴权/断网/密钥脱敏通过；用户未提供真实供应商密钥，真实兼容未验证 | test_stage2.py,test_stage8_combinations.py |
| AC-22 | 通过 | 回答模型切换无重建，向量ID保持，generation记录新模型快照 | test_stage8_faults.py |
| AC-23 | 通过 | 向量语义/维度不同禁止混用；切片变化待重建；失败半成品不激活 | test_stage4.py |
| AC-24 | 通过（隔离方式） | api容器OUTPUT拒绝外网，放行本地Ollama/DNS；真实导入问答成功；浏览器只加载本地静态资源。未全机断网 | test_stage8_docker.py / e2e本地资产 |
| AC-25 | 通过 | 模拟模型停服/API断网保留问题，不回退、不重放新请求 | test_stage8_faults.py,test_stage8_combinations.py |
| AC-26 | 部分验证 | 进程重启、浏览器刷新、实际容器删除重建全部数据与索引ID保留；物理电脑重启尚未执行 | test_stage1_docker.py,test_stage8_docker.py |
| AC-27 | 通过 | 只清索引保持文件与对话，needs_rebuild；重建后可召回 | test_stage4.py,test_stage6.py |
| AC-28 | 通过 | 隔离目录完整覆盖恢复，文件/索引/对话/引用保留，无密钥；API缺密钥提示与补填 | test_stage7.py / e2e备份恢复 |
| AC-29 | 通过 | 损坏/格式版本/路径越界均拒绝，原数据不变 | test_stage7.py |
| AC-30 | 通过 | 覆盖前备份；两处交换故障回滚；启动崩溃日志恢复旧数据 | test_stage7.py |
| AC-31 | 通过 | 16GB无GPU真实Docker导入与回答、空间内存记录，无持续崩溃 | stage8-docker-real.json / stage4-resource.json |

真实API和物理电脑重启未标为通过。应用外网隔离验证采用容器防火墙，未关闭整台电脑网络或更改宿主机防火墙。模拟API测试用于协议/失败/混合配置，不能代表真实服务商。

最终全量：47项Pytest测试通过，0失败/0跳过；耗时174.40秒，报告 `.reports/all-tests.xml`。浏览器全量5项通过；前端生产构建、Docker镜像构建通过。

目标机实测：三文件导入约6.29秒，首段输出约6.46秒，完整问答约7.56秒，过程最低可用内存约2098MB。api约93MB/web约10MB（容器内存）；仅本次样本和设备条件，不作性能承诺。

检索基准100/1000/5000条向量，约0.015/0.042/0.409秒，分批查询RSS增量约0.01/1.05/2.82MB。此基准用3维契约向量验证存储批次，真实1024维模型耗时见stage4-resource.json。
