# Exam Diagnosis（考试诊断）

[English](README.md) · **简体中文**

一个本地优先的工具，把学生的考试卷变成一份诊断：哪些题错了、对应哪些知识点、**为什么**错、接下来该练什么、跨越多场考试后趋势如何。提供网页版（FastAPI 后端 + React 前端）和自带后端的 Windows 桌面版（Tauri）。

一切都在你自己的机器上运行。模型推理是可选、可插拔的：任何 OpenAI-compatible 服务商都能用，按角色逐个启用；每一步都有确定性的兜底逻辑，而且兜底就明说是兜底——应用绝不会伪造一个它没有真正拿到的 OCR 结果、诊断或评分。

状态：**0.1.0，alpha。** 机制已完整并有测试覆盖（后端约 400 个测试，CI 在 Linux 和 Windows 上运行）；随仓库发布的公共知识库只是一个占位分区（高中 / 数学 / 广东，14 个知识点）。见[当前限制](#当前限制)。

## 目录

- [功能](#功能)
- [工作原理](#工作原理)
- [快速开始（网页版）](#快速开始网页版)
- [配置](#配置)
  - [模型角色](#模型角色)
  - [默认服务商](#默认服务商)
  - [视觉解析（图片与扫描版 PDF）](#视觉解析图片与扫描版-pdf)
  - [成本控制](#成本控制)
  - [登录与多用户模式](#登录与多用户模式)
  - [全部环境变量](#全部环境变量)
- [桌面客户端（Tauri）](#桌面客户端tauri)
- [手机拍照上传（局域网）](#手机拍照上传局域网)
- [知识库](#知识库)
- [报告、导出与学习闭环](#报告导出与学习闭环)
- [后台任务与可观测性](#后台任务与可观测性)
- [API 概览](#api-概览)
- [开发](#开发)
- [目录结构](#目录结构)
- [当前限制](#当前限制)
- [贡献、安全、许可](#贡献安全许可)

## 功能

- **什么都能传**：文本、PDF（有文字层或扫描件）、图片——从电脑上传，或者扫二维码用手机相机直接经局域网传过来（不经公网，不需要 HTTPS）。
- **解析并确认**：题目被切分出来供人工审核，确认之后才进入分析。每道题记录 机器识别值 → 用户修正值 → 最终确认值 的溯源链。
- **知识点映射**：关键词匹配 + 可选的模型语义匹配，匹配对象是公共知识库叠加用户自己维护的知识库。
- **逐题错因诊断**（条件遗漏 / 计算错误 / 概念不清 / 审题错误 / 书写不规范 / 待人工确认），带错误率和复习优先级的知识树，以及一份逐条说明"这个结论为什么只能算部分成立"的可信度清单。
- **验证题**：每个错因专题生成一道真正的变式题和完整参考答案，经过独立复核门禁——未复核的答案绝不展示给学生。学生作答由模型对照**已复核**的参考答案批改，没配置模型时退回自评。
- **复习计划**（1/3/7/16/35 天的间隔重复阶梯）、**错题本**（含隐藏答案的练习版）、**跨考试趋势**。
- **导出**：Markdown、HTML、PDF、Word 从同一份中间结构渲染，遵守同一条复核门禁；个人数据可打包导出 / 导入。
- **桌面客户端**自带后端，有设置页（key 永远不出现在任何接口返回里）、系统状态页和带预算、限速的模型调用日志。
- **可选的账号系统**：默认本地单用户零配置；需要时启用 token 登录并按用户隔离数据。

## 工作原理

```
上传 ──► 解析（文本 / PDF / 视觉）──► 审核并确认题目 ──► 确认考试
                                                              │
   复习计划 ◄── 验证题 ◄── 报告 ◄── 分析任务 ◄──────────────────┘
   错题本                    │
   跨考试趋势                └── 错因 · 知识树 · 优先级
```

- **解析**按页进行。文本和带文字层的 PDF 直接读取；图片和扫描版 PDF（没有文字层）在配置了视觉解析器时走视觉解析，否则该页标记 `manual_confirmation_required`。科目与考试不符的页面会被排除出统计，但保留并列出。
- **确认**是信任边界：只有确认过的题目进入分析。撤销确认会把考试版本加一，并把旧的题目记录标记为 `superseded`（从不删除）；报告始终写明它依据的是哪个版本。
- **分析**把每道题映射到知识点、诊断错题、构建知识树，按错误**率**计算 `review_priority`（1 错 1 比 10 错 1 更靠前），把**课程范围**和**本次实际考查范围**分开呈现，并汇总 `quality_flags`。
- **模型按角色可选**（见[模型角色](#模型角色)）。没有 key 的角色走规则兜底，输出会明说——比如正则切题的结果一律标 `review_required`。

## 快速开始（网页版）

要求：Python 3.11+，Node.js 20+（CI 用 24）。Rust 只在打桌面包时需要。

```bash
git clone <本仓库> exam_diagnosis
cd exam_diagnosis
python -m pip install -e .[dev]
```

启动后端（只提供 JSON API，监听 `127.0.0.1:8765`）：

```bash
uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765
```

启动前端（第一次先 `npm --prefix frontend install`）：

```bash
npm --prefix frontend run dev
```

打开 <http://localhost:5173/>。不配任何模型 key 时整条流程都走确定性兜底，足够把每个页面看一遍。

要启用模型推理，可以在**设置页**（`/settings`，保存到 `<data_dir>/config.json`，保存即生效）里填 key，或者把 [`.env.local.example`](.env.local.example) 复制为 `.env.local`，只填你要启用的角色。

## 配置

### 数据目录

所有可写数据都在一个根目录下：`EXAM_DIAGNOSIS_DATA_DIR`（默认 `./data`）——SQLite 数据库 `exam_diagnosis.sqlite3`（或 `EXAM_DIAGNOSIS_DB`）、`uploads/`、`config.json`。桌面版把它指到用户的应用数据目录。公共知识库 YAML 在 `data/knowledge/`（或 `EXAM_DIAGNOSIS_KNOWLEDGE_DIR`）。

设置的优先级是**环境变量（含 `.env.local`）> `config.json`**。由环境变量设定的项在设置页里锁定——硬存也不会生效，所以接口直接以 `409 locked_by_environment` 拒绝并说明。多用户模式下设置页只读，服务器范围的配置由部署者通过环境变量管理。key 只回报"已设置 / 未设置 + 来源"，永远不回传明文。

### 模型角色

六个文本角色，各自独立、按需启用，通过 `EXAM_DIAGNOSIS_<ROLE>_{API_KEY,BASE_URL,MODEL,TIMEOUT}` 配置。只设 `API_KEY` 即可启用一个角色；`BASE_URL`/`MODEL` 不填时用[默认服务商](#默认服务商)。

| 角色 | 档位 | 做什么 | 未启用时的兜底 |
|---|---|---|---|
| `split` | 快速 | 把页面提取出的文本切分成一道道题 | 内置正则，结果一律标 `review_required` |
| `match` | 快速 | 按语义为题目挑选知识点，与关键词命中合并（`keyword` / `model` / `keyword+model`） | 只有关键词匹配 |
| `analysis` | 推理 | 逐道错题给出根因、解释和规避建议（按题缓存） | 基于关键词的错因 |
| `verify` | 推理 | 每个错因专题生成一道真正的变式题和完整参考答案 | 固定模板 |
| `grade` | 推理 | 对照**已复核**的参考答案批改学生作答（0–1，≥ 0.8 通过，一句具体反馈）；参考答案未复核时绝不启用 | 学生自评 |
| `report` | 快速 | 只读已算好的结构化统计生成概述文字，不允许编造数据 | 无概述的结构化报告 |

所有角色遵循与视觉解析器相同的规则：没配置、请求失败、响应不是合法 JSON 或不满足预期结构 ⇒ 判定为不可用，走兜底。每次调用不论成功、失败还是被拦下都会记录（见[可观测性](#后台任务与可观测性)）；作答记录上的 `graded_by`（`model` / `self`）说明这次是谁判的。

### 默认服务商

所有角色都通过 OpenAI-compatible 的 `/chat/completions` 接口调用。不必逐个角色配置，一次性把它们指向你的服务商即可：

```bash
EXAM_DIAGNOSIS_DEFAULT_BASE_URL=https://api.your-provider.com/v1
EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL=your-reasoning-model   # analysis / verify / grade
EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL=your-fast-model             # split / report / match
```

设置页的**默认服务商**一节是同样这三项。每个角色的解析顺序：`EXAM_DIAGNOSIS_<ROLE>_*` → `EXAM_DIAGNOSIS_DEFAULT_*` → [`llm.py`](exam_diagnosis/llm.py) 里的内置配置（`BUILTIN_PROVIDER`）。`GET /api/system/status` 会显示实际生效的值以及各自的来源（`env` / `config` / `builtin`）。

### 视觉解析（图片与扫描版 PDF）

只有 `EXAM_DIAGNOSIS_VISION_BASE_URL`、`EXAM_DIAGNOSIS_VISION_API_KEY`、`EXAM_DIAGNOSIS_VISION_MODEL` 三项同时配置才启用（设置页也能填）。它发送 OpenAI-compatible 的多模态请求，图片编码为 base64 data URL（按字节自动识别 PNG / JPEG），并要求 JSON 响应——所以模型必须真正支持图片输入。超时 `EXAM_DIAGNOSIS_VISION_TIMEOUT`（默认 60 秒）。

扫描版 PDF（有页面但没有文字层）会被自动识别，逐页渲染成 150 dpi 图片走视觉解析，再把各页结果合并回这一个上传资料（`ocr_text` 按页拼接，题目按页顺序累加，置信度取最差一页）。有任何一页失败，整份资料标 `review_required` 并在 `warning` 里点名是第几页；一页都没成功、或根本没配视觉解析，就退到人工录入并写清原因。每页都是一次付费调用，所以最多处理 `EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES`（默认 30）页。视觉调用以角色名 `vision` 记录，受同一份预算约束。

### 成本控制

两个可选的全局开关，所有角色共用（默认不限制）：`EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET`（当天 UTC 累计成功调用的 token 数）和 `EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE`。被拦下的调用不会碰网络，但仍会记一笔，标 `blocked: true` 和原因（`daily_token_budget_exceeded` / `rate_limited`），然后走该角色的兜底。

### 登录与多用户模式

- `POST /api/auth/register`（用户名 3–32 位 `[a-z0-9_.-]`，密码至少 8 位）、`/login`、`/logout`、`GET /api/auth/me`。Bearer token 在 `EXAM_DIAGNOSIS_SESSION_TTL_HOURS`（默认 720）后过期，退出即作废。密码用 pbkdf2-sha256（20 万轮、随机盐）；库里只存 token 的 sha256；同一用户名连续失败 5 次后 15 分钟内拒绝（429）；"用户不存在"和"密码错误"返回同一个 `invalid_credentials`。
- `EXAM_DIAGNOSIS_AUTH_REQUIRED` 未设（默认）：身份来自请求头 `X-User-Id`（缺省 `local`）——本地单人零配置。带了有效 token 以 token 为准；无效 / 过期的 token 直接 401，不会悄悄退回本地身份。
- `EXAM_DIAGNOSIS_AUTH_REQUIRED=1`：除注册 / 登录外所有接口都必须带有效 token，`X-User-Id` 完全不看。资源归属在一个统一的依赖里检查；别人的资源返回 404 而不是 403。

### 全部环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `EXAM_DIAGNOSIS_DATA_DIR` | `./data` | 所有可写数据的根目录 |
| `EXAM_DIAGNOSIS_DB` | `<data_dir>/exam_diagnosis.sqlite3` | SQLite 文件 |
| `EXAM_DIAGNOSIS_KNOWLEDGE_DIR` | `data/knowledge` | 公共知识库 YAML 目录 |
| `EXAM_DIAGNOSIS_<ROLE>_API_KEY` | 未设 | 启用该角色（`SPLIT`、`MATCH`、`ANALYSIS`、`VERIFY`、`GRADE`、`REPORT`） |
| `EXAM_DIAGNOSIS_<ROLE>_BASE_URL` / `_MODEL` / `_TIMEOUT` | 服务商默认 / 30 秒 | 单角色覆盖 |
| `EXAM_DIAGNOSIS_DEFAULT_BASE_URL` | 内置配置 | 所有角色的接口地址 |
| `EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL` | 内置配置 | `analysis` / `verify` / `grade` 用的模型 |
| `EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL` | 内置配置 | `split` / `report` / `match` 用的模型 |
| `EXAM_DIAGNOSIS_VISION_BASE_URL` / `_API_KEY` / `_MODEL` / `_TIMEOUT` | 未设 / 60 秒 | 视觉解析（三项都要） |
| `EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES` | 30 | 扫描版 PDF 的页数上限 |
| `EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET` | 不限 | 每日 token 预算（UTC） |
| `EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE` | 不限 | 限速 |
| `EXAM_DIAGNOSIS_TASK_WORKERS` | 1 | 后台工作线程数 |
| `EXAM_DIAGNOSIS_AUTH_REQUIRED` | 0 | 是否强制登录 |
| `EXAM_DIAGNOSIS_SESSION_TTL_HOURS` | 720 | token 有效期 |
| `EXAM_DIAGNOSIS_BACKEND_EXE` | 自动 | 桌面壳：显式指定后端程序路径 |

## 桌面客户端（Tauri）

`frontend/src-tauri/` 是同一套 React 前端的 Tauri 2 壳（Windows 用系统自带的 WebView2），Python 后端由 PyInstaller 冻结后作为资源打进安装包。需要 Rust 工具链（`cargo`）、`frontend/` 下的 `npm install`，以及 `pip install -e .[desktop]`。

```bash
python scripts/build_backend.py                 # 冻结后端 → frontend/src-tauri/resources/backend/，并做冒烟测试
npm --prefix frontend run desktop:build         # 安装包在 frontend/src-tauri/target/release/bundle/
npm --prefix frontend run desktop:dev           # 带热重载的开发窗口
```

`build_backend.py` 产出一个 onedir 包（约 100 MB），然后真的把它跑一遍：建任务 → 确认 → 报告 → 导出 PDF 和 Word → 生成一次二维码。PyInstaller 会悄悄漏掉数据文件（python-docx 模板、PyMuPDF 原生库、知识库 YAML、二维码的 SVG 工厂），而这些只会在运行期才炸，所以冒烟测试不是可选项。

运行时，壳启动 `exam-diagnosis-backend --port 0 --data-dir <AppData> --parent-pid <壳的 pid>`，读它打出的 `LISTENING host:port`，前端通过 Tauri 命令 `api_base` 拿到地址，再轮询 `/api/health` 确认就绪才显示界面。壳正常退出时杀掉后端；壳被强杀时，后端靠父进程看门狗自己退出（Windows 上等父进程句柄，其它平台轮询 pid），不会留下占着端口的孤儿进程。子进程的环境里剔除了所有 `EXAM_DIAGNOSIS_*_API_KEY`：桌面版的 key 一律来自设置页写的 `config.json`。Windows 上数据在 `%APPDATA%\com.examdiagnosis.desktop\`。没找到内置后端时（开发态），壳不拉起任何进程，前端退回 `127.0.0.1:8765`。

**硬约束：任何功能都不能依赖浏览器提供的东西。** 桌面窗口里没有后退按钮、地址栏、下载管理器、新标签页，也没有 F5。具体落实：

- 导航：每个页面顶部有全局导航条（返回 / 诊断台 / 知识库 / 系统状态 / 设置 / 当前身份）；"返回"用应用内路由历史。登录不强制时登录页提供"不登录，直接使用"。
- 下载：所有导出（PDF/Word/Markdown/HTML/YAML/JSON）都走 `saveBlob()`——桌面端弹原生"另存为"再由 Rust 命令 `save_file` 写盘，浏览器里才用 `<a download>`。**不要用 `<a href>` 直链导出接口**：WebView 会把整个应用页面替换成响应内容且回不来，而且直链不带登录态。
- 不用 `window.location.assign/reload`、`window.open`、`target=_blank`。会话失效是一个 `exam-diagnosis:auth-lost` 事件，由路由处理。
- 外部资源：只有 Google Fonts，离线时退化到系统字体。后端 CORS 放行 WebView 来源（`http://tauri.localhost`、`tauri://localhost`）；CSP 放行本机任意端口，因为后端端口是动态的。

## 手机拍照上传（局域网）

拍试卷是手机的事，应用跑在电脑上。这条通道把两边接起来，**不经过公网**：

1. 在考试页点「用手机拍照上传」→ 出二维码（列出本机全部候选 IP——一台机器常有 Wi-Fi、有线、VPN、WSL 多个地址，猜错了却不告诉用户是最差的失败方式）。
2. 手机连同一个 Wi-Fi 扫码 → 打开由后端提供的一个自包含小页面（`/m/<token>`），用 `<input type="file" capture="environment">` 唤起系统相机——不走 `getUserMedia`，所以纯 http 就行，不需要证书。
3. 文件直接落到这场考试上；电脑上实时看到份数增加；点「传完了，开始解析」。

安全模型：主服务始终只监听 `127.0.0.1`。开通道时另起一个 `0.0.0.0` 的监听，通道关闭或到期（15 分钟）就停。从这个监听进来的请求只允许访问 `/m/*` 和 `/api/mobile-upload/*`，其它一律 403——判定依据是**请求从哪个端口进来的**，所以你自己把 web 部署绑到 `0.0.0.0` 不受影响。二维码里的令牌一次性、只限这一场考试（只能上传；库里只存 SHA-256）。令牌只在生成时给出一次；页面刷新后界面会说明并提供「重新生成」。Windows 第一次会弹防火墙授权——允许**专用网络**。

## 知识库

两层：随仓库发布、放在 `data/knowledge/` 的 YAML 分区构成的**公共知识库**（按 `stage / subject / province / curriculum_year` 分区），以及每个用户存在 SQLite 里、按用户隔离的**自维护知识库**。

- 分区 YAML 声明 `stage`、`subject`、`province`、`curriculum_year`、`academic_year_range`、`version`、`knowledge_points`；同一组键只允许一个分区。新增 YAML 即可启用新的阶段 / 科目 / 省份 / 课标——代码不写死随仓库发布的那个分区。知识点带 `grade_scope`、`dependencies`、关键词和置信度，加载时全部校验。
- 用户库要么**扩展**公共分区（`knowledge_id` 相同即覆盖公共定义，比如补关键词），要么是产品没覆盖的科目 / 地区的**独立库**。解析时始终优先用户库；`GET /api/knowledge-partitions` 是用户视角（`source: public|user`），合并后的知识点带 `origin`。
- 管理接口：`GET/POST /api/knowledge-bases`，`GET/PUT/DELETE /api/knowledge-bases/{id}`，`POST .../points`（新增或覆盖），`DELETE .../points/{knowledge_id}`，`POST /api/knowledge-bases/import`（与公共库同格式的 YAML），`GET .../export`。每次写入都完整校验（422 带原因）；公共库更新导致某个用户库不再合法时，解析时跳过它并在 `problem` 字段说明原因。
- 创建任务必须指向存在的分区（`422 knowledge_partition_not_found` 附可用分区列表），年级必须在分区的 `academic_year_range` 内（`422 grade_outside_partition_scope`），学年标签（`25-26` 或 `2025-2028`）必须至少覆盖一整学年。任务、报告、导出都记录所用的分区、课标、版本和学年。

内容建设是后续阶段的事；机制已经就绪。

## 报告、导出与学习闭环

- **报告**：错题、带证据的错因、规避建议、知识树（`question_count`、`wrong_count`、`error_rate`、`review_priority`、`priority_score`）、`review_order`、`scope_coverage`（每个课程知识点 `assessed_with_errors` / `assessed_without_errors` / `not_assessed`——最后一个只表示**本次试卷没考到**，不是**没掌握**）、`quality_flags`（资料不完整、页面需人工确认、置信度偏低、页面被排除、题目未映射、得分未录入，每条带 `code`、`severity`、`message`）、`exam_versions`，以及完整度清单（错题、原题、参考答案、学生作答、教师批改）。
- **复核门禁**：生成后跑一个独立的 `report.review` 步骤，检查知识点映射、证据引用和每道验证题的答案。不通过的标 `needs_review`：答案在所有地方都被隐去（接口返回 `answer_withheld` + `withheld_reason`；导出显示占位说明）。`POST /api/verification-questions/{id}/review` 传 `{approved, answer, note}` 解除；空答案不允许通过（`422 cannot_approve_empty_answer`）。
- **导出**：`GET /api/reports/{run_id}/export?format=md|html|pdf|docx`。PDF 用 PyMuPDF 的 Story 排版引擎和自带的中文回退字体（装了 fontTools 时做子集化），A4 自动分页；Word 用 python-docx 的内置标题 / 列表样式。两者渲染的是与 Markdown 版相同的块结构。
- **验证与掌握**：每次作答保存答案、得分、是否通过、用时和 `graded_by`。掌握状态按"知识点 + 错误簇"维护：`not_started` → `in_progress` → `provisionally_resolved` → `resolved`，失败回退；所有尝试保留历史。
- **复习计划**：每个条目一条间隔重复阶梯（1、3、7、16、35 天），由每次作答推进或重置；导航条显示到期数量。
- **错题本**：按任务或全部，按知识点分组，有隐藏答案的练习版；可导出。
- **趋势**：一个学生跨考试的错误率、错因构成和知识点变化，并给出方向判断（好转 / 恶化 / 持平，阈值 0.2）。零错题的考试也可以确认，正是为了让曲线能降到 0。
- **可移植**：`GET /api/submissions/{id}/export`（个人数据 JSON）、zip **打包**导出 / 导入（导入时重新映射 id 并防止路径穿越）、级联删除（同时清掉 `uploads/` 下属于该任务的文件）。
- **判重**：同一考试内字节相同的文件无论文件名都会被拒绝，并返回已存在资源的 id、文件名和 sha256。

## 后台任务与可观测性

- 解析（逐页视觉调用）和报告生成（多次模型调用）在进程内的工作线程上跑，不占着 HTTP 请求：`POST /api/jobs/{job_id}/enqueue` 和 `POST /api/analysis-runs/{run_id}/generate` 立刻返回 `202` 和 `task_id`；轮询 `GET /api/tasks/{task_id}`（`queued` / `running` / `completed` / `failed`），`GET /api/jobs/{job_id}` 看各阶段进度，或 `GET /api/analysis-runs/{run_id}/report`（好之前返回 `202 report_generating`）。重复排队被拒绝（`409 job_already_enqueued`）或返回同一个任务。同步的 `POST /api/jobs/{job_id}/run` 保留给脚本。
- 不依赖 Redis / Celery：`EXAM_DIAGNOSIS_TASK_WORKERS` 个线程（默认 1——工作主要是等模型，多个并行只会互相抢同一份预算）。任务记录存在 SQLite；重启时仍在 `queued` / `running` 的任务标 `failed`（`error: process_restarted`），不会留下永远"运行中"的幽灵任务。
- `GET /api/model-calls?role=&limit=`：当前用户的模型调用记录（角色、模型、延迟、成功 / 失败 / 被拦下、错误、token）、汇总统计和当前 `budget` 状态。日志上限 500 条。
- `GET /api/system/status`：每个角色（是否配置、实际使用的模型 / 接口 / 超时、环境变量前缀——绝不带 key）、实际生效的默认服务商及其来源、登录模式、任务数、预算拦截原因、存储路径、当前用户视角的知识库分区、可选特性（`qr_code`）。`/status` 页面把这些放在一起。
- `GET /api/health`：公开的存活检查，返回版本、数据目录和 `auth_required`。

## API 概览

所有接口都是 `/api/` 下的 JSON；后端除了 `/m/` 下的手机上传页不提供任何页面。主流程：

```
POST /api/submissions                                   创建任务（阶段 / 科目 / 省份 / 年级 / 学年）
POST /api/submissions/{sid}/exams                       添加考试
POST /api/exams/{eid}/upload-sessions                   开一个上传会话
POST /api/upload-sessions/{sid}/assets                  上传文件（multipart）· POST .../finish 创建解析作业
POST /api/jobs/{jid}/enqueue                            后台解析 · GET /api/jobs/{jid} · POST .../retry
GET  /api/exams/{eid}/pages · PATCH /api/pages/{pid}     材料类型 / include_in_analysis
GET  /api/exams/{eid}/parse-draft · PATCH .../parse-draft/questions/{i}    审核切分出的题目
POST /api/exams/{eid}/confirm                           确认（422 questions_required / wrong_question_original_required）
POST /api/exams/{eid}/reopen · /reparse                 版本加一并标记旧记录 superseded · 重新解析
PATCH /api/question-records/{rid}                       得分、教师批注
POST /api/submissions/{sid}/analysis-runs → POST /api/analysis-runs/{rid}/confirm-start → /generate
GET  /api/analysis-runs/{rid}/report · GET /api/reports/{rid}/export?format=md|html|pdf|docx
GET  /api/analysis-runs/{rid}/verification-questions · POST /api/verification-questions/{vid}/attempts · /review
GET  /api/submissions/{sid}/trends · /wrong-questions · /mastery-records · GET /api/review-plan
GET  /api/submissions/{sid}/export · POST /api/submissions/import       可移植的打包导出 / 导入
POST/GET/DELETE /api/exams/{eid}/mobile-upload           手机上传通道
GET  /api/knowledge-partitions · /api/knowledge-bases…
GET  /api/system/status · /api/model-calls · /api/tasks · GET/PUT /api/settings · GET /api/health
```

后端运行时 `/docs` 有 FastAPI 的交互式文档。

## 开发

```bash
python -m pip install -e .[dev]
python -m pytest -q                 # 后端测试，不需要任何模型 key，绝不产生付费调用
npm --prefix frontend run lint      # oxlint
npm --prefix frontend run build     # tsc -b && vite build
python scripts/check.py             # 三步全跑，和 CI 完全一样（--backend / --frontend 只跑一边）
```

- 测试从不访问网络：`tests/conftest.py` 会从环境里清掉所有 `EXAM_DIAGNOSIS_*_API_KEY` 和 `EXAM_DIAGNOSIS_DEFAULT_*`，`app.py` 在 pytest 下拒绝加载 `.env.local`。每个测试用独立的 SQLite 文件。
- CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在 Ubuntu 和 Windows 上跑后端测试（父进程看门狗两个平台实现不同），前端跑 `npm ci → lint → build`。
- `scripts/verify_server.py` 对一个运行中的服务跑完整流程；`scripts/build_backend.py --smoke-only` 重跑打包后端的冒烟测试。
- 前端页面按路由懒加载；720 px 以下响应式；antd 的提示通过 `src/feedback.tsx` 转发，以便在 `App` 上下文内渲染。

## 目录结构

```
exam_diagnosis/        FastAPI 后端
  app.py               路由、任务接线、中间件
  storage.py           基于 SQLite 的映射
  llm.py               按角色的 OpenAI-compatible 客户端、服务商默认值
  parser.py            文本 / PDF / 视觉解析
  knowledge.py         公共 + 用户知识库、匹配
  analysis.py, report.py, export.py, trends.py, review_plan.py, wrong_book.py, transfer.py
  auth.py, settings.py, paths.py, env_file.py
  mobile.py, lan_listener.py     手机上传通道
  tasks.py             进程内任务队列
  server.py            桌面版入口（--port 0, --data-dir, --parent-pid）
frontend/              React + TypeScript + Vite + antd
  src-tauri/           Tauri 2 桌面壳（Rust）
data/knowledge/        公共知识库分区（YAML）
scripts/               build_backend.py, check.py, verify_server.py, backend_entry.py
tests/                 pytest 测试
```

## 当前限制

以下是**现在仍然成立**的限制；做掉的项目会从这里删掉。

- 公共知识库只随仓库提供一个分区（广东高中数学），14 个占位知识点。其它阶段、科目、地区需要补对应 YAML 才能创建任务。
- 图片和扫描版 PDF 的识别依赖外部 OpenAI-compatible 视觉服务；没配置时明确标记为待人工确认，不会伪造 OCR 成功。
- 自动批改是模型对照已复核参考答案的判断，不是符号计算意义上的数学判定；参考答案未复核时退回自评。
- 桌面版没有自动更新，也没有代码签名（首次运行会被 SmartScreen 拦一次）。
- 数据只在本机——没有同步；换机器靠导出 / 导入。
- 删除任务会级联删除其考试、题目、分析、验证与掌握记录，并擦除 `uploads/` 下属于它的文件。

## 贡献、安全、许可

- [CONTRIBUTING.md](CONTRIBUTING.md)——环境搭建、测试与提交变更。
- [SECURITY.md](SECURITY.md)——漏洞报告；应用保护什么、不保护什么。
- [CHANGELOG.md](CHANGELOG.md)。
- 许可：[Apache License 2.0](LICENSE)。
