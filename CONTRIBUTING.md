# Contributing

**English** · [简体中文](#贡献指南)

Thanks for taking the time. This document covers setting up, the rules the code follows, and how to get a change merged.

## Setup

```bash
python -m pip install -e .[dev]        # backend + test dependencies
npm --prefix frontend install          # frontend
python scripts/check.py                # backend tests + frontend lint + build — must pass before you open a PR
```

Run the backend with `uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765` and the frontend with `npm --prefix frontend run dev`. Keep any real model keys in `.env.local` (gitignored); never commit one.

## Rules the code follows

1. **Never fake success.** A model role or parser that is unconfigured, fails, or returns malformed output is *unavailable*: return `None`, take the deterministic fallback, and say so in the output (`review_required`, `manual_confirmation_required`, `graded_by: self`). Do not invent OCR text, diagnoses, grades or narrative.
2. **Secrets never leave the process.** API keys are reported as set/unset plus their source. No endpoint, log line, error message or export may contain one.
3. **Tests never make paid calls.** `tests/conftest.py` scrubs credentials and `app.py` refuses `.env.local` under pytest. A test that needs a client sets a fake key with `monkeypatch.setenv` and stubs the HTTP call.
4. **The frontend must not depend on browser chrome.** The desktop window has no back button, address bar, tabs or download manager. Use the in-app nav and router, `saveBlob()`/`downloadFromApi()` for every download, and never `<a href>` to an API URL, `window.location`, `window.open` or `target=_blank`. Verify desktop-affecting changes with the WebView origin in mind (`Origin: http://tauri.localhost`), not just in a browser tab.
5. **Ownership is one check.** Any route that takes a resource id goes through the resource-owner dependency; another user's resource is a 404.
6. **Persistence is SQLite via `SQLiteMapping`.** New state lives in a mapping registered in `_PERSISTENT_MAPPINGS` so the per-request flush and background-task flush cover it.
7. **Every feature ships with tests**, and a change to a documented behaviour updates `README.md` *and* `README.zh-CN.md`. The "Current limitations" section lists what is true today — remove an item when you make it false.

## Making a change

- One logical change per commit; explain *why* in the message body, not just what. Chinese or English are both fine.
- Backend: keep the style of the surrounding code (comments explain motivation, not mechanics). Add tests under `tests/` for the behaviour, including the failure path.
- Frontend: TypeScript strict, antd 6 components, `oxlint` clean of *new* warnings. Pages are lazy-loaded routes in `src/App.tsx` and reachable from `AppNav`.
- Desktop: if you change the backend's runtime footprint (new dependency, new data file, new lazily imported module), extend `scripts/build_backend.py` (`HIDDEN_IMPORTS`, `COLLECT_DATA`, `datas()`) and make the smoke test exercise it.
- Knowledge partitions (`data/knowledge/*.yaml`) must pass the loader's validation (`pytest tests/test_knowledge*.py`); one partition per `stage/subject/province/curriculum_year`.

Open a pull request against `master`. CI runs the backend tests on Linux and Windows and the frontend lint/build; all of it must be green.

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, the output of `GET /api/system/status` with anything sensitive removed (it contains no keys by design), and the relevant lines of `GET /api/model-calls` or `/api/tasks` if a model or background task was involved. For security issues see [SECURITY.md](SECURITY.md).

---

# 贡献指南

[English](#contributing) · **简体中文**

感谢你的时间。本文说明如何搭环境、代码遵循的规则，以及如何让改动被合并。

## 环境

```bash
python -m pip install -e .[dev]        # 后端 + 测试依赖
npm --prefix frontend install          # 前端
python scripts/check.py                # 后端测试 + 前端 lint + 构建——提 PR 前必须通过
```

后端用 `uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765` 启动，前端用 `npm --prefix frontend run dev`。真实的模型 key 放在 `.env.local`（已被 gitignore）里，永远不要提交。

## 代码遵循的规则

1. **绝不伪造成功。** 没配置、调用失败或返回格式不对的模型角色 / 解析器就是*不可用*：返回 `None`，走确定性兜底，并在输出里明说（`review_required`、`manual_confirmation_required`、`graded_by: self`）。不要编造 OCR 文本、诊断、评分或概述。
2. **密钥不出进程。** API key 只回报"已设置 / 未设置 + 来源"。任何接口、日志、错误信息、导出文件都不能包含 key。
3. **测试绝不产生付费调用。** `tests/conftest.py` 清掉凭据，`app.py` 在 pytest 下拒绝加载 `.env.local`。需要客户端的测试用 `monkeypatch.setenv` 设一个假 key 并 stub 掉 HTTP 调用。
4. **前端不能依赖浏览器提供的东西。** 桌面窗口没有后退按钮、地址栏、标签页、下载管理器。用应用内导航和路由，所有下载走 `saveBlob()` / `downloadFromApi()`，绝不用 `<a href>` 直链 API、`window.location`、`window.open` 或 `target=_blank`。影响桌面版的改动要按 WebView 的视角验证（`Origin: http://tauri.localhost`），而不只是在浏览器标签页里。
5. **归属检查只有一处。** 任何带资源 id 的路由都走资源归属依赖；别人的资源返回 404。
6. **持久化是 SQLite，通过 `SQLiteMapping`。** 新增的状态放进注册在 `_PERSISTENT_MAPPINGS` 里的映射，这样请求中间件和后台任务的落盘都能覆盖到。
7. **每个功能都带测试**；改了文档描述过的行为要同时更新 `README.md` 和 `README.zh-CN.md`。"当前限制"一节列的是现在成立的事——做掉了就删掉。

## 提交改动

- 一个提交只做一件事；提交信息正文解释*为什么*，不只是做了什么。中英文都可以。
- 后端：保持周围代码的风格（注释解释动机，不解释机制）。在 `tests/` 下为该行为补测试，包括失败路径。
- 前端：TypeScript strict，antd 6 组件，不新增 `oxlint` warning。页面是 `src/App.tsx` 里的懒加载路由，并能从 `AppNav` 到达。
- 桌面版：如果改了后端的运行足迹（新依赖、新数据文件、新的惰性导入模块），同步扩展 `scripts/build_backend.py`（`HIDDEN_IMPORTS`、`COLLECT_DATA`、`datas()`），并让冒烟测试覆盖到。
- 知识库分区（`data/knowledge/*.yaml`）必须通过加载器校验（`pytest tests/test_knowledge*.py`）；每组 `stage/subject/province/curriculum_year` 只允许一个分区。

向 `master` 提 pull request。CI 会在 Linux 和 Windows 上跑后端测试，以及前端的 lint / 构建；全部必须通过。

## 报告 bug

开 issue 时请附：你做了什么、期望什么、实际发生了什么、`GET /api/system/status` 的输出（去掉你认为敏感的内容——它按设计不含任何 key），以及涉及模型或后台任务时 `GET /api/model-calls` 或 `/api/tasks` 的相关记录。安全问题见 [SECURITY.md](SECURITY.md)。
