"""설정 저장소, API 키 암호화, 간단한 마크다운 렌더러."""
import base64
import hashlib
import html
import re
from functools import wraps

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app
from flask_login import current_user
from markupsafe import Markup

from .extensions import db
from .models import Setting

DEFAULT_SETTINGS = {
    "site_title": "파이썬 코딩 연습장",
    "announcement": "",
    "require_quiz": "0",          # 1이면 퀴즈를 모두 맞혀야 코딩 문제 제출 가능
    "show_hidden_cases": "1",     # 1이면 채점 케이스의 입력/기대 출력을 학생에게 공개
    "default_password": "",       # 비어 있으면 학번을 초기 비밀번호로 사용
    "allow_custom_run": "1",      # 직접 입력 실행 허용
    "ai_enabled": "0",
    "ai_provider": "gemini",      # gemini | claude | openai
    "ai_model_gemini": "gemini-2.5-flash",
    "ai_model_claude": "claude-sonnet-5-5",
    "ai_model_openai": "gpt-5-mini",
    "ai_key_gemini": "",
    "ai_key_claude": "",
    "ai_key_openai": "",
    "ai_daily_limit": "30",       # 학생 1인당 하루 질문 수 (0 = 무제한)
    "ai_allow_code": "0",         # 1이면 AI가 정답 코드를 직접 보여줄 수 있음
    "ai_extra_prompt": "",
    "ai_max_tokens": "1200",
}


def get_setting(key, default=None):
    row = db.session.get(Setting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key, default) if default is None else default
    return row.value


def get_flag(key):
    return get_setting(key) == "1"


def set_setting(key, value):
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)


def _fernet():
    key = hashlib.sha256(("pyjudge:" + current_app.config["SECRET_KEY"]).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode() if text else ""


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def mask_key(key):
    if not key:
        return ""
    return key[:4] + "•" * 8 + key[-4:] if len(key) > 10 else "•" * 8


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


# ---------------- 마크다운 (안전한 부분 집합) ----------------
_inline_code = re.compile(r"`([^`]+)`")
_bold = re.compile(r"\*\*(.+?)\*\*")


def _inline(text):
    parts = []
    last = 0
    for m in _inline_code.finditer(text):
        parts.append(_bold.sub(r"<strong>\1</strong>", html.escape(text[last:m.start()])))
        parts.append("<code>" + html.escape(m.group(1)) + "</code>")
        last = m.end()
    parts.append(_bold.sub(r"<strong>\1</strong>", html.escape(text[last:])))
    return "".join(parts)


def render_md(text):
    """코드 블록, 인라인 코드, 굵게, 제목, 목록, 표 없는 문단만 지원. 모든 HTML은 이스케이프된다."""
    if not text:
        return Markup("")
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    para = []
    list_type = None

    def flush_para():
        nonlocal para
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para = []

    def close_list():
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para(); close_list()
            lang = stripped[3:].strip()
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            cls = f' class="lang-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>" + html.escape("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_para(); close_list()
            level = min(len(m.group(1)) + 2, 6)
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
        elif re.match(r"^[-*]\s+", stripped):
            flush_para()
            if list_type != "ul":
                close_list(); out.append("<ul>"); list_type = "ul"
            out.append("<li>" + _inline(re.sub(r"^[-*]\s+", "", stripped)) + "</li>")
        elif re.match(r"^\d+[.)]\s+", stripped):
            flush_para()
            if list_type != "ol":
                close_list(); out.append("<ol>"); list_type = "ol"
            out.append("<li>" + _inline(re.sub(r"^\d+[.)]\s+", "", stripped)) + "</li>")
        elif stripped == "":
            flush_para(); close_list()
        else:
            close_list()
            para.append(line)
        i += 1
    flush_para(); close_list()
    return Markup("\n".join(out))
