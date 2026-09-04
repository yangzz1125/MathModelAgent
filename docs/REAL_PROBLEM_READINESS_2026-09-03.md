# 真题测试就绪检查（2026-09-03）

## 结论

当前 `pi-integration` 分支已具备启动一轮全新 CUMCM 真题测试的运行条件。此次检查没有运行完整建模任务，只验证控制面、固定 fixture、已完成 workspace、绘图与论文工具链。

## 已接入

- Vue 导入、配置、任务页、文件下载、论文预览、暂停、恢复和终止。
- Pi RPC bridge，默认 Sol high 负责规划/审查，Luna high 负责执行/写作。
- Contract-v3 Problem Inventory → Inventory Audit → 逐问题 Method Card → Feasibility Spike → Method Audit → Candidate → Scientific Review → Paper Planning → Diagram → Writing → Document Verification。
- Host-only acceptance、严格 Reviewer JSON、逐问题受控修订、A→B 降级授权、修复预算、阶段边界、SHA-256 lineage 和重启恢复。
- `figure_specs`：Planner 预先声明 claim、目的、图型、reference、面板、编码、注释、尺寸和产物路径。
- 31 项正式绘图参考，其中 12 项来自 Seaborn 官方 gallery，1 项专门表达首次事件认证括号；所有参考图均为 `evidence_eligible=false`。
- 图表真实数据、生成器、矢量母版、PNG 预览、灰度和最终尺寸检查。
- claim/evidence 驱动的论文规划、manifest、引用检查、版面检查和 Document Verification。

## 本轮修复

1. Windows 上传路径防护：拒绝 drive-relative、盘符绝对路径、UNC、遍历、NTFS alternate stream 和 NUL 路径。
2. 文档工具前置检查：Start 前验证所选 `xelatex`/`typst` 与 `pdftoppm`；失败时项目保持 `ready`，不调用模型。
3. 删除旧前端 API Key 对话框、API 和 localStorage 持久化 store；浏览器不再保留可误启用的凭据路径。
4. 移除未实现的 zip 输入选项；继续支持文件夹和 PDF/Markdown/Text/CSV/XLSX/DOCX/PNG/JPEG。
5. 将 12 个 Seaborn 网络模板提升为合法 `network-*` reference ID，同时禁止直接运行官方 demo 数据作为证据。

## 本轮增量规划升级

1. 新任务使用 contract-v3；Problem Inventory、Method Card、Spike 和 Method Audit 全部按问题/版本持久化，旧版本不覆盖。Inventory/Method Card 的缺文件或 schema 错误最多在原 session、原版本局部修复两次，不消耗语义 attempt；只有独立 Reviewer 拒绝才创建新版本，越界写入或 frozen evidence 变化仍立即失败。
2. Reviewer Pi session（含最终 Document Reviewer）只激活 `read/grep/find/ls`；`bash/powershell/edit/write` 与 extension tools 从模型工具集合中移除，Host transaction marker 另有签名防篡改。所有 strict-JSON Reviewer 的协议错误都只在原只读 session 修正一次，不增加 audit/scientific attempt、不触发 Producer repair、不创建语义版本，且计数跨暂停恢复持久化；第二次格式错误以 `review_protocol` 失败。Document Reviewer 只返回严格 JSON，Host 复核后生成 `reports/VERIFY_REPORT.md`。
3. 方法规划与正式执行逐问题交错；下游 Spike 可以读取已科学接受的上游代码和结果。Host 在 prompt 中提供 stage context list 以约束正常相关性和减少搜索：Inventory 指向全部输入，之后 Method/Spike/Execution/Reviewer 指向当前题声明输入、accepted dependencies 与适用的 Method/Spike/candidate lineage；Execution 和 Scientific Review 只拿已选 figure-reference entry/preview。该 list 不是文件系统保密或路径访问边界；真正强制的完整性控制是 Reviewer 工具类型限制、Windows Host locks、确定性写入范围检查和 frozen hashes。模型仍不得搜索 `pi/*.py`、tests、其他 workspace、历史 Git 或未列出的旧 Method/Spike 版本；Paper Planning/Writing/Verify 使用对应的 paper context list。
4. evidence level 固定为 `A_certified`、`B_bounded_numerical`、`C_exploratory`；Level C 不能覆盖题面 requested output。
5. 主 Spike 预算为正式问题预算的 10%，下限 20 秒、上限 120 秒；最多一次 60 秒补充探针，累计 bash 时间跨暂停持久化。Host 校验或预算错误最多进行两次同 Method 版本局部修复，不消耗 Method revision；耗尽即失败，越界写入或冻结证据变化立即失败。
6. Method Audit 初稿加最多两次普通定向修订；耗尽后默认失败，仅保留一次 Reviewer 明确授权、Host 校验的 A→B 校准。
7. Host 增量组装 schema-v2 `execution_plan.json`，最终生成并复核 `reports/PLAN_COMPLETENESS.json`。
8. contract-v1/v2 历史 workspace 不迁移，paused v2 继续原恢复路径；失败 A 题 workspace `3c2fd38e601b` 不恢复。contract-v3 启动后聊天区仅展示输出，Bridge 与前端共同拒绝自由 prompt，用户控制只通过 pause/resume/cancel，避免 steer 干扰确定性阶段。

## 验证证据

- `python -m unittest discover -s pi/tests -p 'test_*.py'`：98 tests passed（含 contract-v3、Reviewer capability、Host 双槽 journal/强制锁、Job Object 与失败注入测试）。
- `validate_single_bakery.py workspaces/387f2e0b2668`：`SINGLE_BAKERY_PASS`。
- 使用 `pdftoppm` 实际渲染已完成论文第一页：通过。
- `python -m compileall`：通过。
- `pip check`：无损坏依赖。
- `npm run lint`：199 个前端源码文件通过；5.3 MiB 的上游 notebook 静态 fixture 被明确排除。
- `vue-tsc -b && vite build`：通过，2427 modules transformed。
- `git diff --check`：通过，仅有既有 LF/CRLF 提示。
- Secret pattern scan：未发现 `sk-...` 密钥。
- 31 项 reference catalog：全部可读取，12 个 `network-*` 项和 1 个 event-threshold 项已注册。

## 当前环境

- Python 3.11.6
- Pi 0.84.4
- SciencePlots 2.2.2
- Seaborn 0.13.2
- adjustText 1.4.0
- XeLaTeX / TeX Live 2025
- Poppler `pdftoppm` 25.02.0
- Node 24.13.0
- pnpm 11.22.0
- 中文字体：Source Han Serif SC
- 磁盘 E: 可用约 267 GB

服务：

- Bridge：`http://127.0.0.1:8000`
- Frontend：`http://127.0.0.1:5173/chat`
- 个人版 Bridge 无认证，受支持的 `start_web.ps1` 只允许 `127.0.0.1`；直接使用 Uvicorn/Vite 绑定非 loopback 地址不受支持且不安全。

## 真题启动参数

1. 选择解压后的官方赛题文件夹，不上传 zip。
2. 点击“初始化项目”，核对识别出的主题目、数据集数量和参考文件。
3. CUMCM 选择“全国赛 / 中文 / LaTeX”。
4. 规划/审查选择 `openai/gpt-5.6-sol` + `high`。
5. 执行/写作选择 `openai/gpt-5.6-luna` + `high`。
6. 点击“开始执行”。浏览器可以刷新或关闭；不要终止 bridge/frontend 服务终端。
7. 临时停止使用“暂停”；“终止”是不可恢复的终态。

## 运行中检查点

- Inventory 后应出现 versioned `planning/inventory/v<n>/problem_inventory.json`，Inventory Audit accept 后才开始 q1 方法设计。
- 每个问题必须依次产生 Method Card、有限预算 Spike 和 Method Audit；Host 接受后才把该题追加到 schema-v2 `execution_plan.json`。
- 每个 requested output 必须由 Level A 或 B claim 覆盖；Level C 只能作为补充探索。
- 每个问题先产生正式 `candidate`，再由独立 Scientific Review accept。Spike 只能作为规划 benchmark，不能作为论文结果证据。
- 图表 provenance 必须引用当前问题真实数据，不能引用 `skills/`、`previews/`、examples 或 `*_replica`。
- 所有问题接受且 `reports/PLAN_COMPLETENESS.json` 通过后才进入 Paper Planning。
- 最终只有 `reports/VERIFY_REPORT.md` 明确独立 `PASS` 且 PDF 可读时，任务才显示完成。

## 非阻塞风险

- 上游文件保留混合 LF/CRLF，`git diff --check` 仅给出换行转换提示；全目录 lint 和生产构建均通过，不影响运行。
- Draw.io 未安装；它是可选项，Diagram 阶段必须在不需要概念图时写明省略理由。
- Typst 未安装；选择 Typst 会在 Start 前被明确拒绝。当前应选择 LaTeX。
- 已暴露的 DeepSeek API key 轮换仍是外部待办；本仓库未检出该密钥。
- 历史 workspaces 保留 completed/failed/cancelled/paused 证据；新真题会创建独立 contract-v3 workspace，不会复用它们。
- 本次升级没有启动第二轮 A 题；Bridge 必须重启后，新任务才会加载 contract-v3。
