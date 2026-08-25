# Exam Diagnosis MVP

本仓库是独立于 `D:/selfProject/DeepTutor` 的本地单用户考试诊断 MVP，当前默认演示范围为：高中 / 数学 / 广东。

## 启动

在仓库根目录执行：

```bash
PYTHONPATH=. uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765
```

浏览器打开：`http://127.0.0.1:8765/`

## 测试

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
- 视觉接口使用 OpenAI-compatible `/chat/completions` 多模态请求，图片编码为 base64 data URL，并要求 JSON 响应。
- 未配置视觉解析器、请求失败或响应 JSON 无法解析时，结果明确标记为 `manual_confirmation_required`，不会伪造 OCR 成功。

## 知识库分区

- `GET /api/knowledge-partitions` 返回全部可用分区及其版本、课程标准和知识点数量。
- 首页“知识库分区”下拉框由该接口渲染，用户无法选择没有知识库的阶段/科目/省份组合。
- 分区键是 `stage + subject + province + curriculum_year`；同一地区同一科目可以并存多套课程方案，创建任务不传 `curriculum_year` 时默认取最新的一套。
- 分区的 `academic_year_range` 声明它覆盖哪些学年（如 `[高一, 高二, 高三]`）。分区内知识点的 `grade_scope` 必须落在这个范围内，否则加载即报错；创建任务时年级不在范围内会返回 422 `grade_outside_partition_scope`。
- 创建任务时若 `stage/subject/province/curriculum_year` 没有对应分区，接口返回 422 `knowledge_partition_not_found` 并附带可用分区列表，不会创建出一个永远匹配不到知识点的任务。
- 学年范围形如 `25-26` 或 `2025-2028`，必须至少覆盖一整学年；不足一学年（如 `25-25`）会被拒绝并返回规则说明，跨多学年则允许，任务上记录 `academic_year_start/end/span`。
- 任务、分析报告和个人数据导出都记录所用分区（阶段、科目、省份、课程方案年份、覆盖学年、知识库版本、课程标准），知识点匹配结果也带上分区标识，便于追溯。

## 异科目与页面取舍

- 解析出的科目与任务科目不符时，页面标记 `subject_mismatch`，`include_in_analysis` 置为 `false`，其题目不会进入题目草稿，也就不会进入解析统计。
- 被排除的页面不会被删除，`GET /api/exams/{eid}/pages` 的 `excluded_pages` 会列出文件名、排除原因、判定方是系统还是用户，以及少算了多少道题。
- 用户可以 `PATCH /api/pages/{page_id}` 传 `include_in_analysis` 显式覆盖：把误判的异科目页面重新纳入，或把不想统计的页面排除。覆盖后题目草稿立即重算，已经手工修改过的草稿内容会被保留。
- 确认考试后 `completeness.excluded_pages` 会一并记录本次分析漏掉了哪些页面。

## 页面与得分编辑

- 首页“解析确认”里可以直接改页序、材料类型、是否计入解析统计，对应 `PATCH /api/pages/{page_id}`。
- 考试确认后出现“得分与批改”列表，可以填每题得分、满分和教师批注，对应 `PATCH /api/question-records/{record_id}`，失分自动计算。

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
