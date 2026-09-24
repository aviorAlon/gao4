# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

**English** · [简体中文](#更新日志)

## [Unreleased]

## [0.1.0] — 2026-09-24

First public release. Local-first exam diagnosis for the web (FastAPI + React) and Windows desktop (Tauri with an embedded backend).

### Added

- Upload of text, PDF (text layer or scanned) and images; per-page parsing with a human confirmation step and a machine → corrected → confirmed provenance chain per question.
- Phone photo upload over the LAN through a one-shot, exam-scoped QR token, served on a separate on-demand listener that admits only the upload routes.
- Knowledge-point matching (keyword + optional model), root-cause diagnosis, knowledge tree with error rates and review priorities, curriculum-scope vs. assessed-scope coverage, quality flags, exam versioning.
- Verification questions with an independent review gate; model grading against reviewed answers only, self-assessment otherwise; mastery tracking.
- Spaced-repetition review plan, wrong-question book (with practice version), cross-exam trends.
- Exports: Markdown, HTML, PDF (PyMuPDF Story, CJK fallback font), Word (python-docx); personal data JSON and zip bundle export/import.
- Six independently configurable model roles (`split`, `match`, `analysis`, `verify`, `grade`, `report`) plus a vision parser, all OpenAI-compatible; provider-wide defaults `EXAM_DIAGNOSIS_DEFAULT_BASE_URL` / `_REASONING_MODEL` / `_FAST_MODEL` and a settings-page section for them.
- Settings page backed by `config.json` (environment wins; env-owned fields are locked), system-status page, model-call log with daily token budget and per-minute rate limit.
- Background task queue with restart recovery.
- Optional accounts (pbkdf2, bearer tokens, login throttling) and a multi-user mode with central ownership enforcement.
- Desktop shell: embedded PyInstaller backend, dynamic port, parent-process watchdog, native save dialogs, key-scrubbed child environment; `scripts/build_backend.py` with a real smoke test.
- CI on Linux and Windows; `scripts/check.py` runs the same checks locally.
- Bilingual documentation (English / 简体中文).

---

# 更新日志

[English](#changelog) · **简体中文**

本项目所有值得注意的变更都记录在这里。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] — 2026-09-24

首个公开版本。本地优先的考试诊断，提供网页版（FastAPI + React）和 Windows 桌面版（Tauri，自带后端）。

### 新增

- 上传文本、PDF（有文字层或扫描件）和图片；按页解析，带人工确认步骤，每道题记录 机器识别 → 用户修正 → 最终确认 的溯源链。
- 手机经局域网拍照上传：一次性、限定考试的二维码令牌，由按需开启、只放行上传路由的独立监听提供服务。
- 知识点匹配（关键词 + 可选模型）、错因诊断、带错误率和复习优先级的知识树、课程范围与实际考查范围的覆盖对比、质量提示、考试版本管理。
- 带独立复核门禁的验证题；只对照已复核答案做模型批改，否则自评；掌握状态跟踪。
- 间隔重复复习计划、错题本（含练习版）、跨考试趋势。
- 导出：Markdown、HTML、PDF（PyMuPDF Story，中文回退字体）、Word（python-docx）；个人数据 JSON 与 zip 打包导出 / 导入。
- 六个可独立配置的模型角色（`split`、`match`、`analysis`、`verify`、`grade`、`report`）加视觉解析器，全部 OpenAI-compatible；服务商级默认值 `EXAM_DIAGNOSIS_DEFAULT_BASE_URL` / `_REASONING_MODEL` / `_FAST_MODEL` 及对应的设置页一节。
- 基于 `config.json` 的设置页（环境变量优先；环境变量拥有的项锁定）、系统状态页、带每日 token 预算和每分钟限速的模型调用日志。
- 带重启恢复的后台任务队列。
- 可选账号系统（pbkdf2、bearer token、登录限流）和统一归属检查的多用户模式。
- 桌面壳：内嵌 PyInstaller 后端、动态端口、父进程看门狗、原生保存对话框、剔除 key 的子进程环境；`scripts/build_backend.py` 带真实冒烟测试。
- Linux 和 Windows 上的 CI；`scripts/check.py` 在本地跑同一套检查。
- 中英双语文档。
