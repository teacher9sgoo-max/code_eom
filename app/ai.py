"""생성형 AI 도우미. 관리자가 Gemini / Claude / OpenAI(GPT) 중 하나를 선택해 활성화한다."""
import requests

from .utils import decrypt, get_flag, get_setting

PROVIDERS = {
    "gemini": "Google Gemini",
    "claude": "Anthropic Claude",
    "openai": "OpenAI GPT",
}
TIMEOUT = 60


class AIError(Exception):
    pass


def current_config():
    provider = get_setting("ai_provider")
    if provider not in PROVIDERS:
        provider = "gemini"
    return {
        "enabled": get_flag("ai_enabled"),
        "provider": provider,
        "model": get_setting(f"ai_model_{provider}"),
        "key": decrypt(get_setting(f"ai_key_{provider}")),
        "max_tokens": int(get_setting("ai_max_tokens") or 1200),
    }


def system_prompt(problem=None):
    allow_code = get_flag("ai_allow_code")
    rules = [
        "당신은 중학생·고등학생에게 파이썬을 가르치는 친절한 AI 학습 도우미입니다.",
        "항상 한국어로, 학생 눈높이에 맞게 짧고 명확하게 설명합니다. 답변은 보통 10줄 이내로 합니다.",
        "코드나 출력 예시는 ```python 코드 블록으로 감쌉니다. 마크다운 표와 HTML은 쓰지 않습니다.",
        "학생이 틀렸을 때는 무엇이 왜 틀렸는지 원인을 짚고, 스스로 고칠 수 있도록 단계적인 힌트를 줍니다.",
        "파이썬 학습·이 사이트의 문제와 무관한 요청은 정중히 거절하고 학습으로 안내합니다.",
    ]
    if allow_code:
        rules.append("학생이 원하면 정답 코드를 보여줄 수 있지만, 먼저 힌트를 주고 코드에는 줄마다 설명을 붙입니다.")
    else:
        rules.append("문제의 정답이 되는 완성 코드는 절대 제공하지 않습니다. 학생 코드의 틀린 줄을 지적하거나, "
                     "문제와 다른 예시로 개념을 보여주는 짧은 코드(3줄 이내)만 허용됩니다.")
    extra = (get_setting("ai_extra_prompt") or "").strip()
    if extra:
        rules.append("교사 추가 지침: " + extra)
    text = "\n".join("- " + r for r in rules)
    if problem is not None:
        text += (f"\n\n[현재 문제] {problem.number}. {problem.title}\n{problem.description}\n"
                 f"[입력] {problem.input_desc}\n[출력] {problem.output_desc}")
        if problem.concept:
            text += f"\n[이 문제의 학습 개념]\n{problem.concept}"
    return text


def _gemini(cfg, system, messages):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['model']}:generateContent"
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
            "generationConfig": {"maxOutputTokens": cfg["max_tokens"], "temperature": 0.4}}
    r = requests.post(url, json=body, timeout=TIMEOUT, headers={"x-goog-api-key": cfg["key"]})
    data = _json(r)
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise AIError("Gemini 응답을 해석할 수 없습니다: " + str(data)[:300])


def _claude(cfg, system, messages):
    body = {"model": cfg["model"], "max_tokens": cfg["max_tokens"], "system": system, "messages": messages}
    r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=TIMEOUT,
                      headers={"x-api-key": cfg["key"], "anthropic-version": "2023-06-01",
                               "content-type": "application/json"})
    data = _json(r)
    try:
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
    except (KeyError, TypeError):
        raise AIError("Claude 응답을 해석할 수 없습니다: " + str(data)[:300])


def _openai(cfg, system, messages):
    body = {"model": cfg["model"], "messages": [{"role": "system", "content": system}] + messages,
            "max_completion_tokens": cfg["max_tokens"]}
    r = requests.post("https://api.openai.com/v1/chat/completions", json=body, timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {cfg['key']}"})
    data = _json(r)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise AIError("OpenAI 응답을 해석할 수 없습니다: " + str(data)[:300])


def _json(r):
    try:
        data = r.json()
    except ValueError:
        raise AIError(f"AI 서버 응답 오류 (HTTP {r.status_code})")
    if r.status_code >= 400:
        msg = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(msg, dict):
            msg = msg.get("message", msg)
        raise AIError(f"AI 서버 오류 (HTTP {r.status_code}): {str(msg)[:300]}")
    return data


def ask(system, messages, cfg=None):
    """messages: [{"role": "user"|"assistant", "content": str}] → 응답 문자열"""
    cfg = cfg or current_config()
    if not cfg["key"]:
        raise AIError("API 키가 설정되지 않았습니다.")
    if not cfg["model"]:
        raise AIError("모델 이름이 설정되지 않았습니다.")
    fn = {"gemini": _gemini, "claude": _claude, "openai": _openai}[cfg["provider"]]
    try:
        text = fn(cfg, system, messages)
    except requests.RequestException as e:
        raise AIError(f"AI 서버에 연결할 수 없습니다: {e}")
    return text.strip() or "(응답이 비어 있습니다. 다시 질문해 주세요.)"
