"""Phone → desktop upload over the local network.

Taking a photo of a test paper is something you do with a phone; the app runs on a computer. This
closes that gap without going through the public internet: the desktop shows a QR code, the phone
(on the same Wi-Fi) opens a tiny upload page served by this backend, and the photos land directly
in the exam being worked on.

Three decisions worth knowing about:

**A second listener, not a wider bind.** The main server keeps listening on 127.0.0.1 only. When a
phone-upload session starts, a *separate* uvicorn listener opens on 0.0.0.0 and closes again when
the session ends, so the API is reachable from the LAN only during the minutes it's needed.
Requests arriving on that listener are additionally restricted (see `is_lan_request` and the
middleware in app.py) to the phone-upload routes — even while it's open, nothing else is exposed.

**A scoped one-time token, not open access.** The QR carries a token bound to one exam, valid for
`DEFAULT_TTL_MINUTES`. It can add files to that exam and nothing else: it can't read reports, list
submissions, or touch settings. Only its SHA-256 is stored, so a copy of the database is not a bag
of live credentials.

**The upload page is served from here, not from the React app.** In the desktop build the frontend
lives inside a WebView and isn't reachable over HTTP at all, so a phone has nothing to point at
except this backend. The page is deliberately one self-contained file with no framework: it loads
instantly over Wi-Fi and uses `<input type="file" capture>`, which opens the system camera without
needing a secure context — that's what makes plain http on a LAN workable with no certificates.
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import secrets
import socket
from datetime import datetime, timedelta
from html import escape
from typing import Any

DEFAULT_TTL_MINUTES = 15
TOKEN_BYTES = 24
MOBILE_PATH_PREFIX = '/m/'
MOBILE_API_PREFIX = '/api/mobile-upload/'

SECURITY_NOTE = (
    '这个通道开着时，同一个 Wi-Fi 下拿到二维码的人可以往这场考试上传文件（也仅限于此，'
    '读不到报告、看不到其它数据）。家里的网络没问题；公共 Wi-Fi 下建议用完就关。'
)


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_key(token: str) -> str:
    """Sessions are stored under the token's hash — the database never holds a usable token."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def expiry(minutes: float = DEFAULT_TTL_MINUTES, now: datetime | None = None) -> str:
    return ((now or datetime.utcnow()) + timedelta(minutes=minutes)).isoformat() + 'Z'


def is_expired(session: dict[str, Any], now: datetime | None = None) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(session.get('expires_at', '')).rstrip('Z'))
    except ValueError:
        return True
    return (now or datetime.utcnow()) >= expires_at


def session_is_open(session: dict[str, Any] | None, now: datetime | None = None) -> bool:
    return bool(session) and not session.get('closed') and not is_expired(session, now)


# --- where the phone should connect ---------------------------------------------------------------


def lan_addresses() -> list[str]:
    """Candidate IPv4 addresses this machine can be reached at from the LAN.

    A machine usually has several (Wi-Fi, Ethernet, VPN, Docker, virtual adapters) and there's no
    reliable way to know which one the phone shares — so this returns all plausible ones, best
    guess first, and the UI lets the user switch if the first doesn't work. Guessing silently and
    being wrong is the failure mode worth avoiding.
    """
    candidates: list[str] = []

    def add(address: str) -> None:
        try:
            parsed = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError:
            return
        if parsed.is_loopback or parsed.is_link_local or parsed.is_multicast or parsed.is_unspecified:
            return
        if address not in candidates:
            candidates.append(address)

    # The routing table's own answer for "which interface would reach the outside world" is
    # usually the one the phone is on. No packet is sent — a UDP socket connect() only sets a peer.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('192.0.2.1', 9))  # TEST-NET-1, guaranteed not to be a real host
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    # Private ranges first: a public or CGNAT address is almost never the one to hand a phone.
    return sorted(candidates, key=lambda address: 0 if ipaddress.IPv4Address(address).is_private else 1)


def upload_urls(port: int, token: str, addresses: list[str] | None = None) -> list[str]:
    return [f'http://{address}:{port}{MOBILE_PATH_PREFIX}{token}' for address in (addresses if addresses is not None else lan_addresses())]


def qr_svg(text: str) -> str | None:
    """QR as inline SVG, or None if it can't be produced.

    Generated here rather than in the browser so the phone page and the desktop UI share one
    implementation and it can be tested without a DOM. Returns None instead of raising when the
    library is unavailable (a frozen build that lost it, say): the URL is displayed alongside the
    code anyway, so a missing QR is an inconvenience, not a broken feature.
    """
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return None
    try:
        image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buffer = io.BytesIO()
        image.save(buffer)
        return buffer.getvalue().decode('utf-8')
    except Exception:
        return None


def is_lan_request(scope: dict[str, Any], lan_port: int | None) -> bool:
    """True when this request arrived on the LAN listener rather than the loopback one.

    Keyed on the accepting socket's port, not on the client address: the web deployment may
    legitimately be bound to 0.0.0.0 by its operator, and restricting *those* requests would break
    the perfectly normal case of opening the app from another computer on purpose. Only the
    listener this module opens gets locked down.
    """
    if not lan_port:
        return False
    server = scope.get('server') or ()
    return len(server) >= 2 and server[1] == lan_port


def path_allowed_from_lan(path: str) -> bool:
    return path.startswith(MOBILE_PATH_PREFIX) or path.startswith(MOBILE_API_PREFIX)


# --- the phone page --------------------------------------------------------------------------------


UPLOAD_PAGE_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 20px 16px 48px; font-family: -apple-system, 'PingFang SC', 'Noto Sans SC', sans-serif;
       background: #faf9f5; color: #1c1e21; -webkit-text-size-adjust: 100%; }
h1 { font-size: 20px; margin: 0 0 4px; }
.kicker { font-size: 11px; letter-spacing: .18em; text-transform: uppercase; color: #63666b; margin-bottom: 10px; }
.meta { color: #63666b; font-size: 14px; margin: 0 0 20px; }
.pick { display: block; width: 100%; padding: 18px; margin-bottom: 12px; border: 1.5px solid #c7391f;
        border-radius: 2px; background: #c7391f; color: #fff; font-size: 17px; text-align: center; }
.pick.secondary { background: #fff; color: #c7391f; }
input[type=file] { display: none; }
ul { list-style: none; padding: 0; margin: 16px 0 0; }
li { display: flex; justify-content: space-between; gap: 10px; padding: 10px 12px; background: #fff;
     border: 1px solid #ded9cb; margin-bottom: 8px; font-size: 14px; word-break: break-all; }
li .state { flex: none; color: #63666b; }
li.ok .state { color: #1f6f5c; }
li.fail .state { color: #c7391f; }
.done { margin-top: 24px; padding: 14px; width: 100%; border: 1px solid #ded9cb; background: #fff; font-size: 15px; }
.note { margin-top: 20px; font-size: 12px; color: #63666b; line-height: 1.6; }
.banner { padding: 12px; border: 1px solid #eab8ac; background: #fbeae6; color: #1c1e21; font-size: 14px; margin-bottom: 16px; }
"""


def upload_page(token: str, exam_name: str, uploaded: int = 0) -> str:
    """Self-contained page: no build step, no framework, no external requests.

    `capture="environment"` opens the camera directly. It is used instead of getUserMedia on
    purpose — getUserMedia needs a secure context, which a plain-http LAN address is not, and
    working around that would mean self-signed certificates and a trust prompt on the phone.
    """
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>上传到 {escape(exam_name)}</title>
<style>{UPLOAD_PAGE_CSS}</style>
</head><body>
<div class="kicker">Exam Diagnosis</div>
<h1>上传到「{escape(exam_name)}」</h1>
<p class="meta">拍照或从相册选择，文件会直接传到电脑上。已传 <b id="count">{uploaded}</b> 份。</p>
<div id="banner" class="banner" hidden></div>

<label class="pick" for="camera">拍照上传</label>
<input id="camera" type="file" accept="image/*" capture="environment" multiple>

<label class="pick secondary" for="album">从相册 / 文件选择</label>
<input id="album" type="file" accept="image/*,application/pdf" multiple>

<ul id="list"></ul>
<button class="done" id="done" type="button">传完了，关闭这个通道</button>
<p class="note">这个链接只能往这一场考试上传文件，有效期到时会自动失效。关掉之后电脑上重新点「手机上传」可以再开一个。</p>

<script>
const TOKEN = {token!r};
const list = document.getElementById('list');
const countEl = document.getElementById('count');
const banner = document.getElementById('banner');
let count = {uploaded};

function say(text) {{ banner.textContent = text; banner.hidden = !text; }}

async function send(file) {{
  const row = document.createElement('li');
  row.innerHTML = '<span></span><span class="state">上传中…</span>';
  row.firstChild.textContent = file.name || '照片';
  list.prepend(row);
  const body = new FormData();
  body.append('file', file, file.name || 'photo.jpg');
  try {{
    const res = await fetch(`/api/mobile-upload/${{TOKEN}}/assets`, {{ method: 'POST', body }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${{res.status}}`);
    row.className = 'ok';
    row.lastChild.textContent = '已上传';
    countEl.textContent = ++count;
  }} catch (err) {{
    row.className = 'fail';
    row.lastChild.textContent = String(err.message || err);
    say('有文件没传成功，可以重试；如果一直失败，可能是通道已经过期。');
  }}
}}

for (const id of ['camera', 'album']) {{
  document.getElementById(id).addEventListener('change', async (event) => {{
    say('');
    const files = [...event.target.files];
    event.target.value = '';
    for (const file of files) await send(file);
  }});
}}

document.getElementById('done').addEventListener('click', async () => {{
  await fetch(`/api/mobile-upload/${{TOKEN}}`, {{ method: 'DELETE' }}).catch(() => {{}});
  document.body.innerHTML = '<div class="kicker">Exam Diagnosis</div><h1>已关闭</h1>'
    + '<p class="meta">共上传 ' + count + ' 份，回电脑上继续。</p>';
}});
</script>
</body></html>"""


def expired_page(reason: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>链接已失效</title><style>{UPLOAD_PAGE_CSS}</style></head><body>
<div class="kicker">Exam Diagnosis</div>
<h1>链接已失效</h1>
<p class="meta">{escape(reason)}</p>
<p class="note">回到电脑上重新点「手机上传」，会生成一个新的二维码。</p>
</body></html>"""
