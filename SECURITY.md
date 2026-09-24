# Security policy

**English** · [简体中文](#安全政策)

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use the repository's private vulnerability reporting (GitHub → *Security* → *Report a vulnerability*) if it is enabled, or contact a maintainer directly. Include steps to reproduce, the version (`GET /api/health` or the desktop "About"), and the platform. You should hear back within 7 days.

## What the application does and does not protect

Exam Diagnosis is a **local-first, single-user-by-default** application. Understanding its threat model helps decide what counts as a vulnerability.

**Designed guarantees**

- The API server binds to `127.0.0.1` only. The phone-upload channel opens a second listener on `0.0.0.0` on demand; requests arriving through it can reach only `/m/*` and `/api/mobile-upload/*` and everything else is refused (403), based on the listening port, not on headers. The channel closes on demand or after 15 minutes.
- Phone-upload tokens are one-shot, scoped to one exam, upload-only, and stored as SHA-256.
- API keys are never returned by any endpoint or written to any export or log. `config.json` is written atomically and made owner-readable where the OS supports it.
- Passwords use pbkdf2-sha256 (200 000 rounds, random salt); session tokens are stored as SHA-256; login is throttled per username; user enumeration is not possible through error codes.
- In multi-user mode (`EXAM_DIAGNOSIS_AUTH_REQUIRED=1`) every non-auth endpoint requires a valid token, ownership is enforced centrally, and another user's resource is indistinguishable from a missing one (404).
- Import bundles are checked for path traversal before extraction; uploaded files are stored under the data directory by generated names.
- The test suite scrubs all credentials and makes no network calls.

**Out of scope / known non-goals**

- Traffic between the frontend and the local backend is plain HTTP on the loopback interface (and on the LAN for the phone-upload channel). There is no TLS; do not expose the port to untrusted networks.
- The single-user mode trusts the `X-User-Id` header by design. Anyone who can reach `127.0.0.1:8765` on the machine is the user. Enable `EXAM_DIAGNOSIS_AUTH_REQUIRED=1` before sharing a server.
- The desktop build is not code-signed and has no auto-update.
- Model providers receive question text (and images, for vision) — review your provider's data policy. Nothing is sent when no role is configured.

If you find a gap between this description and the code's behaviour, that is a vulnerability — please report it.

---

# 安全政策

[English](#security-policy) · **简体中文**

## 报告漏洞

安全问题请不要开公开 issue。如果仓库启用了私密漏洞报告（GitHub → *Security* → *Report a vulnerability*）请使用它，否则直接联系维护者。请附复现步骤、版本（`GET /api/health` 或桌面版"关于"）和平台。7 天内会回复。

## 应用保护什么、不保护什么

Exam Diagnosis 是一个**本地优先、默认单用户**的应用。了解它的威胁模型有助于判断什么算漏洞。

**设计上的保证**

- API 服务只绑定 `127.0.0.1`。手机上传通道按需另起一个 `0.0.0.0` 的监听；从它进来的请求只能访问 `/m/*` 和 `/api/mobile-upload/*`，其它一律拒绝（403），判定依据是监听端口而不是请求头。通道按需关闭或 15 分钟后自动关闭。
- 手机上传令牌一次性、限定一场考试、只能上传，库里只存 SHA-256。
- API key 不会被任何接口返回，也不会写进任何导出或日志。`config.json` 原子写入，并在操作系统支持时设为仅所有者可读。
- 密码用 pbkdf2-sha256（20 万轮、随机盐）；会话 token 以 SHA-256 存储；按用户名限流登录；无法通过错误码枚举账号。
- 多用户模式（`EXAM_DIAGNOSIS_AUTH_REQUIRED=1`）下所有非认证接口都要求有效 token，归属统一检查，别人的资源与不存在的资源无法区分（404）。
- 导入包在解压前检查路径穿越；上传文件以生成的文件名存放在数据目录下。
- 测试套件清除所有凭据，不访问网络。

**不在范围内 / 已知的非目标**

- 前端与本地后端之间是回环接口上的明文 HTTP（手机上传通道则是局域网）。没有 TLS；不要把端口暴露到不可信网络。
- 单用户模式按设计信任 `X-User-Id` 请求头。能访问本机 `127.0.0.1:8765` 的人就是用户。共享服务器前先开 `EXAM_DIAGNOSIS_AUTH_REQUIRED=1`。
- 桌面版没有代码签名，也没有自动更新。
- 模型服务商会收到题目文本（视觉解析时还有图片）——请查看你所用服务商的数据政策。没配置任何角色时不会发送任何东西。

如果你发现代码行为与这里的描述不一致，那就是漏洞——请报告。
