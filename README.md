# Exam Diagnosis MVP

本仓库是独立于 `D:/selfProject/DeepTutor` 的本地单用户考试诊断 MVP，当前默认演示范围为：高中 / 数学 / 广东。

## 启动

后端只提供 JSON API，不再自带页面（早期版本 `/` 会返回一份内嵌 HTML 的临时界面，已经被下面的独立前端取代并从代码里删掉）。

在仓库根目录启动后端。PowerShell（Windows 默认终端）：

```powershell
$env:PYTHONPATH = "."
uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765
```

cmd.exe：

```cmd
set PYTHONPATH=.
uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765
```

bash / git-bash / WSL：

```bash
PYTHONPATH=. uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765
```

再启动前端（`frontend/` 是独立的 React + Vite 项目，第一次要先 `npm install`）：

```bash
npm --prefix frontend run dev
```

浏览器打开：`http://localhost:5173/`

## 测试

PowerShell：

```powershell
$env:PYTHONPATH = "."
pytest -q
```

cmd.exe：

```cmd
set PYTHONPATH=.
pytest -q
```

bash / git-bash / WSL：

```bash
PYTHONPATH=. pytest -q
```

## 数据与配置

- SQLite 默认位置：`data/exam_diagnosis.sqlite3`
- 可通过 `EXAM_DIAGNOSIS_DB` 指定数据库路径。
- 上传文件默认写入 `data/uploads/`，该目录被 `.gitignore` 忽略。
- 知识库资源目录：`data/knowledge/`，可通过 `EXAM_DIAGNOSIS_KNOWLEDGE_DIR` 指定。
- 目录下每个 YAML 是一个知识库分区，必须声明 `stage`、`subject`、`province`、`curriculum_year`、`academic_year_range`、`version` 和 `knowledge_points`；同一组 `stage/subject/province/curriculum_year` 只允许一个分区。
- 当前随仓库发布的分区只有 `high_math_guangdong_v1.yaml`（高中 / 数学 / 广东 / 2017 课标），但代码不写死该分区，新增 YAML 即可启用新的阶段、科目、省份或课程方案年份。
- 图片视觉解析只有在同时配置以下环境变量时启用：
  - `EXAM_DIAGNOSIS_VISION_BASE_URL`
  - `EXAM_DIAGNOSIS_VISION_API_KEY`
  - `EXAM_DIAGNOSIS_VISION_MODEL`
- 视觉接口使用 OpenAI-compatible `/chat/completions` 多模态请求，图片编码为 base64 data URL（按字节自动识别 PNG / JPEG），并要求 JSON 响应。需要一个真正支持图片输入的模型（DeepSeek 目前的对话模型不接受图片；比如通义千问 Qwen-VL 的 OpenAI-compatible 接口就可以）。超时默认 60 秒，可用 `EXAM_DIAGNOSIS_VISION_TIMEOUT` 调整。
- 未配置视觉解析器、请求失败或响应 JSON 无法解析时，结果明确标记为 `manual_confirmation_required`，不会伪造 OCR 成功。
- **扫描版 PDF**（手机扫描/复印得到的、没有文字层的 PDF）会被自动识别：文字层为空但有页面的 PDF 不再按"空文本"处理，而是逐页渲染成图片（150 dpi）走视觉解析，再把各页结果合并回这一个上传资料（合并后的 `ocr_text` 按页拼接，题目按页顺序累加，置信度取最差一页）。有任何一页视觉解析没成功，整份资料标 `review_required` 并在 `warning` 里点名是第几页；一页都没成功、或者根本没配视觉解析，就和别的解析失败一样退到人工录入，`warning` 会写清楚原因（"该 PDF 为扫描件……未配置视觉解析"）。每页都是一次付费的视觉调用，所以默认最多识别前 30 页（`EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES` 可改），超出的页数会在 `warning` 里说明。视觉调用和其它角色一样记进 `/api/model-calls`（角色名 `vision`）并受同一份预算/限速约束。

## 模型推理（题目切分 / 错因诊断 / 验证题生成 / 验证题批改 / 报告概述 / 知识点匹配）

- 按角色配置，六个角色互相独立，默认都不开启：
  - `match`（题目→知识点语义匹配）：`EXAM_DIAGNOSIS_MATCH_API_KEY` / `EXAM_DIAGNOSIS_MATCH_BASE_URL` / `EXAM_DIAGNOSIS_MATCH_MODEL` / `EXAM_DIAGNOSIS_MATCH_TIMEOUT`（默认 `deepseek-v4-flash`，见"公共知识库与用户自维护知识库"）
  - `split`（题目切分）：`EXAM_DIAGNOSIS_SPLIT_API_KEY` / `EXAM_DIAGNOSIS_SPLIT_BASE_URL` / `EXAM_DIAGNOSIS_SPLIT_MODEL` / `EXAM_DIAGNOSIS_SPLIT_TIMEOUT`
  - `analysis`（错因诊断）：`EXAM_DIAGNOSIS_ANALYSIS_API_KEY` / `EXAM_DIAGNOSIS_ANALYSIS_BASE_URL` / `EXAM_DIAGNOSIS_ANALYSIS_MODEL` / `EXAM_DIAGNOSIS_ANALYSIS_TIMEOUT`
  - `verify`（验证题生成）：`EXAM_DIAGNOSIS_VERIFY_API_KEY` / `EXAM_DIAGNOSIS_VERIFY_BASE_URL` / `EXAM_DIAGNOSIS_VERIFY_MODEL` / `EXAM_DIAGNOSIS_VERIFY_TIMEOUT`
  - `grade`（验证题批改）：`EXAM_DIAGNOSIS_GRADE_API_KEY` / `EXAM_DIAGNOSIS_GRADE_BASE_URL` / `EXAM_DIAGNOSIS_GRADE_MODEL` / `EXAM_DIAGNOSIS_GRADE_TIMEOUT`
  - `report`（报告概述）：`EXAM_DIAGNOSIS_REPORT_API_KEY` / `EXAM_DIAGNOSIS_REPORT_BASE_URL` / `EXAM_DIAGNOSIS_REPORT_MODEL` / `EXAM_DIAGNOSIS_REPORT_TIMEOUT`
- 只需配置对应角色的 `API_KEY` 即可启用；`BASE_URL`/`MODEL` 不填时默认为 DeepSeek 的 `https://api.deepseek.com`，`analysis`/`verify`/`grade` 默认推理档 `deepseek-v4-pro`，`split`/`report` 默认更便宜的 `deepseek-v4-flash`；换成任意 OpenAI-compatible 服务商只需覆盖这两项。
- `grade` 角色在学生提交验证题作答时调用：拿题干、已复核的参考答案和学生作答让模型批改，给出 0-1 的得分比例和一句具体反馈，"是否通过"以模型的判定为准（≥0.8 通过），学生自评的分数仍然保留在记录里。只对参考答案**已通过独立复核**的验证题启用——拿一份没审过的参考答案去批改，只会把错答案洗成一个看起来可信的分数。未配置/调用失败/参考答案未复核时，退回原来的学生自评判定，作答记录上的 `graded_by` 字段（`model` / `self`）说明这次是谁判的。
- `split` 角色替代文本/PDF 内容原来的正则切题：不同试卷题号格式差异很大（"1."、"第1题"、"1、"……），正则难以覆盖所有情况，交给模型按语义切分更可靠。图片走的视觉解析模型本来就是模型切题，这个角色补齐的是文本/PDF 这条路径。
- `analysis` 角色只对错题调用，按知识点+题目内容判断根因（条件遗漏/计算错误/概念不清/审题错误/书写不规范/待人工确认之一）并给出针对这道题的解释和规避建议，结果按题目 id 缓存，同一题不会重复调用。
- `verify` 角色按错因专题调用，生成一道真正的变式练习题和完整参考答案（含关键步骤），而不是"完成一道变式题并说明关键步骤"这句固定模板；生成的内容仍然要过独立审查阶段（见下）才会把参考答案展示给学生。
- `report` 角色只读取已经算好的结构化统计（错题数、根因分布、复习优先级前几项、质量提示），生成一段面向学生/家长的概述文字，不允许编造数据里没有的信息。
- 五个角色都遵循和视觉解析一致的规则：没配置、请求失败、响应不是合法 JSON 或不满足预期结构时一律判定为不可用，自动退回各自的兜底逻辑，不会伪造成功。`split` 的兜底是内置的正则匹配（覆盖"第N题"/"N题"/"N."/"N、"/"N．"），但正则结果一律标记 `review_required`，需要人工确认切分是否准确、完整——不能因为规则跑完了就当成解析可信；`verify` 的兜底是原来那句固定模板；`grade` 的兜底是学生自评的分数。
- 这些 `API_KEY` 不想每次开新终端都用 `$env:...` 重设的话，可以复制 [`.env.local.example`](.env.local.example) 为同目录下的 `.env.local`，把占位符换成真实 key。`.env.local` 已经在 `.gitignore` 的 `.env.*` 规则里，不会被提交；启动时会自动加载，真实的环境变量（如果两边都设了）优先于文件里的值。

## 后台任务队列

- 解析考试（逐页视觉调用）和生成报告（多次模型调用，推理档模型动辄几十秒）不再占着 HTTP 请求跑：`POST /api/jobs/{job_id}/enqueue` 和 `POST /api/analysis-runs/{run_id}/generate` 都立刻返回 202 和一个 `task_id`，工作在进程内的工作线程上执行。
- 轮询：`GET /api/tasks/{task_id}`（`queued` / `running` / `completed` / `failed`，失败带 `error`）；解析任务也可以直接 `GET /api/jobs/{job_id}` 看每个阶段的状态；报告直接 `GET /api/analysis-runs/{run_id}/report`，生成中返回 202 `report_generating`，好了返回 200。`GET /api/tasks?status=` 列出当前用户的任务。
- 同一个任务不会被重复排队：解析任务排队/运行中再 `enqueue` 或同步 `run` 返回 409 `job_already_enqueued`；报告生成中再 `generate` 直接返回同一个 `task_id`；报告已存在时 `generate` 返回 200。
- 原来的同步接口 `POST /api/jobs/{job_id}/run` 和"直接 GET 报告触发生成"都保留，脚本和测试仍可用；前端已改为后台任务 + 轮询，页面上能看到解析到哪一步。
- 进程内线程池，不依赖 Redis/Celery——本地单用户应用，多一个守护进程没有收益。默认 1 个工作线程（`EXAM_DIAGNOSIS_TASK_WORKERS` 可调）：工作主要是等模型，多个并行只会互相抢同一份 token 预算。
- 任务记录和其它数据一样存在 SQLite 里，工作线程完成后自行落盘（请求中间件的落盘对后台线程不生效）。进程重启时，上次还在 `queued`/`running` 的任务会被标成 `failed`（`error: process_restarted`），对应的解析任务标 `failed` 以便 `retry`，生成中的报告 run 退回 `queued`——不会留下永远"运行中"的幽灵任务。

## 报告导出

- `GET /api/reports/{run_id}/export?format=md|html|pdf|docx`。`md`/`html` 直接返回文本；`pdf`/`docx` 以附件下载（`Content-Disposition: attachment`，文件名 `exam-diagnosis-report-<run_id 前 8 位>.pdf/.docx`）。
- PDF 和 Word 从同一份中间结构（`export.report_blocks`：标题 / 段落 / 列表 / 编号列表）渲染，章节和内容与 Markdown 版一致，包括"未通过复核的验证题答案不展示"这条规则——导出文件里同样只出现占位说明，不会带出未审核的答案。
- PDF 用已有依赖 PyMuPDF 的 Story 排版引擎生成（自带中文回退字体，A4 自动分页），不需要再装 weasyprint/reportlab 这类工具；装了 `fonttools` 时会做字体子集化，否则每份 PDF 会内嵌整套约 3.5MB 的中文字体（内容仍然正确，只是文件大）。Word 用 `python-docx`，标题/列表用 Word 内置样式，中文字体指定为宋体。

## 模型调用可观测性与成本控制

- `GET /api/model-calls`（可选 `role`、`limit` 查询参数）返回当前用户触发过的模型调用记录：角色、模型、延迟、成功与否、失败原因、token 用量，以及汇总统计（总数/成功/失败/平均延迟/总 token）和当前的 `budget` 状态。
- 每次调用不管成功失败都会记一笔，日志表有上限（500 条），超出自动清理最旧的——这是调试用的可观测性，不是需要长期保留的审计日志。
- 两个可选的全局开关（默认都不限制，四个角色共用一份额度，不是分角色各设一个）：
  - `EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET`：当天（UTC）累计成功调用的 token 总数超过这个数，后续调用直接不发起，直接走各角色的兜底逻辑。
  - `EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE`：最近一分钟调用次数达到这个数，同样直接拦下。
- 被拦下的调用不会真的碰网络，但会记一笔到 `/api/model-calls`，标 `blocked: true` 和具体原因（`daily_token_budget_exceeded` / `rate_limited`），不是悄无声息地就是不触发。

## 公共知识库与用户自维护知识库

- 两层：产品自带的**公共知识库**（`data/knowledge/*.yaml`，按阶段/科目/省份/课标年份分区）和每个用户**自己维护的知识库**（存在 SQLite 的 `user_knowledge` 表，按用户隔离）。当前公共库内容只是占位（14 个知识点），内容运营是产品成型后的事，这里做的是机制。
- 用户库两种形态，由 `extends_public` 决定：**扩展公共库**（同一分区的公共知识点做底，用户的知识点叠加；`knowledge_id` 相同即覆盖公共定义，比如给某个知识点补关键词），或**独立库**（产品没覆盖的科目/省份，全部自己定义，必须给 `academic_year_range`）。同一用户同一分区只能有一个用户库（409）。
- 解析规则：`find_partition(..., user_id=)` 先看用户库再看公共库，用户库存在就以它为准。创建任务、知识点匹配、报告的课程覆盖率、导出，用的都是这个"用户视角的有效分区"；`GET /api/knowledge-partitions` 列出的也是用户视角（用户库覆盖的分区标 `source: user`）。`GET /api/knowledge-partitions/points?stage&subject&province` 返回合并后的知识点，每个带 `origin: public|user`。
- 管理接口：`GET/POST /api/knowledge-bases`，`GET/PUT/DELETE /api/knowledge-bases/{id}`，`POST /api/knowledge-bases/{id}/points`（新增或按 `knowledge_id` 覆盖），`DELETE /api/knowledge-bases/{id}/points/{knowledge_id}`，`POST /api/knowledge-bases/import`（上传与公共库同格式的 YAML，可带 `extends_public`/`name`），`GET /api/knowledge-bases/{id}/export`（YAML 下载）。每次写入都做完整校验（字段齐全、`knowledge_id` 唯一、`dependencies` 可解析——扩展公共库时可以引用公共 id、`grade_scope` 在年级范围内、置信度在 0-1），不合法返回 422 和具体原因；公共库更新导致某个用户库不再合法时，解析时跳过它退回公共库，管理接口的 `problem` 字段会说明原因。
- 题目到知识点的匹配现在是**关键词 + 语义**：关键词命中一如既往；配置了 `match` 模型角色（`EXAM_DIAGNOSIS_MATCH_API_KEY`，默认 `deepseek-v4-flash`）时，把这道题和候选知识点列表（id/名称/章节/关键词）交给模型按语义挑选，结果和关键词命中合并——两边都命中的标 `keyword+model`，只有模型认出来的标 `model`（`matched_keywords` 为空，置信度取知识点置信度和模型把握的较小值）。模型不会删掉关键词命中；模型不可用/返回不合法就只剩关键词结果，不会瞎猜。`match` 和其它角色一样记进 `/api/model-calls`、受同一份预算约束。
- 前端 `/knowledge` 页面：看所有分区和知识点，"在此之上建我的库"、添加/覆盖/删除知识点、导入/导出 YAML、删除自己的库（恢复为公共库）。

## 知识库分区

- `GET /api/knowledge-partitions` 返回全部可用分区及其版本、课程标准和知识点数量。
- 首页“知识库分区”下拉框由该接口渲染，用户无法选择没有知识库的阶段/科目/省份组合。
- 分区键是 `stage + subject + province + curriculum_year`；同一地区同一科目可以并存多套课程方案，创建任务不传 `curriculum_year` 时默认取最新的一套。
- 分区的 `academic_year_range` 声明它覆盖哪些学年（如 `[高一, 高二, 高三]`）。分区内知识点的 `grade_scope` 必须落在这个范围内，否则加载即报错；创建任务时年级不在范围内会返回 422 `grade_outside_partition_scope`。
- 创建任务时若 `stage/subject/province/curriculum_year` 没有对应分区，接口返回 422 `knowledge_partition_not_found` 并附带可用分区列表，不会创建出一个永远匹配不到知识点的任务。
- 学年范围形如 `25-26` 或 `2025-2028`，必须至少覆盖一整学年；不足一学年（如 `25-25`）会被拒绝并返回规则说明，跨多学年则允许，任务上记录 `academic_year_start/end/span`。
- 任务、分析报告和个人数据导出都记录所用分区（阶段、科目、省份、课程方案年份、覆盖学年、知识库版本、课程标准），知识点匹配结果也带上分区标识，便于追溯。

## 解析失败与重跑

- 解析作业按考试隔离：一个考试的作业失败不会影响同一任务下其它考试的解析和确认。
- 重跑会先清掉这次考试上一轮的页面再重新解析，不会留下重复页面或重复题目。
- 重跑保留两类用户输入：页面是否计入解析统计的显式判定，以及已经手工修改过的题目草稿（草稿会重新指向新生成的页面）。

## 登录与多用户

- 账号接口：`POST /api/auth/register`（用户名 3-32 位小写字母/数字/`_ . -`，密码至少 8 位）、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`。注册/登录返回一个 bearer token，后续请求带 `Authorization: Bearer <token>`；token 默认 30 天过期（`EXAM_DIAGNOSIS_SESSION_TTL_HOURS` 可调），退出即作废。
- 密码用 pbkdf2-sha256（20 万轮、随机盐）存储，数据库里存的是 token 的 sha256 而不是 token 本身；登录连续失败 5 次后同一用户名 15 分钟内拒绝尝试（429）；"用户不存在"和"密码错误"返回同一个 `invalid_credentials`，不给枚举账号的机会。
- 两种模式，由 `EXAM_DIAGNOSIS_AUTH_REQUIRED` 决定：
  - 不开（默认）：和以前一样，没带 token 就按 `X-User-Id`（缺省 `local`）算身份，本地单人使用零配置。带了 token 以 token 为准；带了**无效/过期**的 token 直接 401，不会悄悄退回本地身份。
  - 开启（`=1`）：除注册/登录外的所有接口都必须带有效 token，`X-User-Id` 完全不看——它只是个谁都能填的请求头，一旦有第二个真人就不能再当身份用。
- 资源归属仍然沿用原来的 `user_id` 字段和 `enforce_resource_owner` 这一个检查点，登录只是换了"当前用户是谁"的来源；别人的资料一律 404。
- 前端：`/login` 页面（登录/注册切换），token 存在 localStorage，请求自动带上；任何接口返回 401 就清掉 token 跳去登录页。页面右上角显示当前身份（"本地用户（未登录）"或账号显示名）和登录/退出。

## 用户归属与资源隔离

- MVP 按本地单用户运行，但领域模型保留了归属边界：任务、考试、资产、上传会话、任务作业、页面、题目记录、分析运行、验证题、掌握记录都带 `user_id`。
- 归属从任务向下继承，请求头 `X-User-Id` 决定当前用户，缺省是 `local`。
- 隔离在一个统一的依赖里检查，任何带资源 id 的路由都会走到，越权访问返回 404 而不是 403，避免泄露资源是否存在。
- `GET /api/submissions` 只返回当前用户自己的任务。

## 异科目与页面取舍

- 解析出的科目与任务科目不符时，页面标记 `subject_mismatch`，`include_in_analysis` 置为 `false`，其题目不会进入题目草稿，也就不会进入解析统计。
- 被排除的页面不会被删除，`GET /api/exams/{eid}/pages` 的 `excluded_pages` 会列出文件名、排除原因、判定方是系统还是用户，以及少算了多少道题。
- 用户可以 `PATCH /api/pages/{page_id}` 传 `include_in_analysis` 显式覆盖：把误判的异科目页面重新纳入，或把不想统计的页面排除。覆盖后题目草稿立即重算，已经手工修改过的草稿内容会被保留。
- 确认考试后 `completeness.excluded_pages` 会一并记录本次分析漏掉了哪些页面。

## 页面与得分编辑

- 考试详情页“页面与材料”里可以直接改材料类型、是否计入解析统计，对应 `PATCH /api/pages/{page_id}`。
- 考试确认后出现“得分与批改”列表，可以填每题得分、满分和教师批注，对应 `PATCH /api/question-records/{record_id}`，失分自动计算。

## 验证题答案复核门禁

- 报告生成后会跑一个独立审查阶段（`report.review`），检查错因专题的知识点映射、证据引用，以及每道验证题的参考答案、知识点和来源题目。
- 审查不通过的验证题标记为 `needs_review`，`GET /api/verification-questions/{id}` 和列表接口都会把 `answer` 置空并给出 `answer_withheld` 与 `withheld_reason`，Markdown/HTML 导出显示“未通过复核，答案暂不展示”。
- 学生仍然可以作答未复核的验证题，被挡住的只是参考答案。
- `POST /api/verification-questions/{id}/review` 传 `{approved, answer, note}` 完成人工复核；空答案不允许通过，返回 422 `cannot_approve_empty_answer`。复核通过后报告里的答案随即放开，分析结果本身不会重算。

## 复习优先级与结论可信度

- 知识树每个节点带 `question_count`、`wrong_count`、`error_rate`、`review_priority` 和 `priority_score`；优先级看错误率不只看错题数，一错一（1/1）比十错一（1/10）更值得复习。
- 报告的 `review_order` 按优先级给出复习顺序，Markdown/HTML 导出里是“复习优先顺序”一节。
- 报告的 `quality_flags` 把散落各处的“这个结论只能算部分成立”统一成一份清单：资料不完整、页面需人工确认、置信度偏低、页面被排除、题目未映射到知识点、得分未录入，每条都带 `code`、`severity` 和给人看的 `message`。
- 导出报告里对应“结论可信度提示”一节；没有任何提示时该节不出现。

## 课程范围与资料覆盖范围

报告的 `scope_coverage` 把知识库分区的课程范围和本次考试资料实际考查到的范围分开呈现，Markdown/HTML 导出同样有这一节：

- 每个课程知识点标记为 `assessed_with_errors`、`assessed_without_errors` 或 `not_assessed`。
- 汇总课程知识点总数、已考查数、未考查数、覆盖率和未考查章节列表。
- `not_assessed` 只表示本次上传的资料没有覆盖该知识点，不能据此判定学生未掌握。

## 解析版本与报告追溯

- 年级统一使用短形式：`高一`/`高二`/`高三`（初中、小学同理，如 `初二`），与知识库 YAML 的 `grade_scope` 保持一致。
- 撤销确认（reopen）会把考试版本加一，并把该考试原有题目记录标记为 `superseded`，保留历史而不删除。
- 分析任务创建时快照所选考试的版本，报告输出 `exam_versions`，Markdown/HTML 导出也会写明依据的版本。
- 分析任务创建后考试又被撤销确认的，生成报告返回 409 `exam_versions_changed`，需要重新确认并新建分析任务；旧报告保持可查看且仍指向旧版本。

## 演示流程

1. 首页选择知识库分区并创建任务。
2. 创建考试。
3. 选择并上传文本、PDF 或图片。
4. 点击“运行解析”。文本走本地解析，PDF走真实文本提取，图片走已配置视觉解析器或人工确认降级。
5. 在解析确认区编辑题干、勾选错题、填写学生答案，并确认原题来源状态。
6. 确认考试。
7. 点击“确认并开始分析”。系统在分析前自动进行知识点映射。
8. 查看包含错题集、错因、规避建议、知识树、优先级和验证题的报告。
9. 可通过以下接口导出：

```text
GET /api/reports/{analysis_run_id}/export?format=md
GET /api/reports/{analysis_run_id}/export?format=html
```

## 学习闭环与数据管理

- 分析报告生成后，验证题作为 SQLite 持久化实体保存，可按分析任务查询。
- 每次验证提交保存答案、得分、是否通过和用时。
- 掌握记录按“知识点 + 错误簇”维护，状态为 `not_started`、`in_progress`、`provisionally_resolved` 或 `resolved`；再次验证失败会回退到 `in_progress`，所有尝试保留历史。
- 支持页面页序/材料类型修改、题目得分与教师批改修改。
- 同一考试内按内容哈希判重：字节相同的图片无论文件名是什么都会被拒绝，并返回已存在资源的 ID、文件名和 sha256；不同考试之间互不影响。
- 题目的机器识别值、用户修正值和最终确认值组成溯源链，随考试确认写入题目记录，并出现在个人数据导出中。
- 考试确认时报告完整度：错题、原题来源、标准答案、学生作答、教师批改五项，缺哪项由 `completeness.missing` 明确列出；缺失不阻塞分析。
- 支持分析历史恢复、任务个人数据 JSON 导出和任务删除。
- 浏览器报告页可直接完成验证题、查看提交结果与掌握状态。

## 当前限制

- 当前是本地单用户 MVP，不包含登录、多用户权限和生产级任务队列。
- 图片识别依赖外部 OpenAI-compatible 视觉服务；没有配置时必须人工确认。
- 验证题目前是基于知识点和错因的可追溯模板题，不是在线题库生成服务。
- PDF 支持文本层提取；纯扫描 PDF 仍需视觉解析器或人工确认。
- 公共知识库目前只随仓库提供广东高中数学一个分区；小学和初中需要补充对应 YAML 后才能创建任务。
- 验证题判分由用户提交的得分决定，当前不提供自动数学答案判定。
- 删除任务会级联删除该任务在本地 SQLite 中的考试、题目、分析、验证与掌握记录，并擦除 `data/uploads/` 下属于该任务的物理文件。
