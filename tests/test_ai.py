"""AI 제공자별 요청/응답 형식 점검 (실제 API 호출 없이 requests.post 를 가짜로 대체)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ai  # noqa: E402


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


def run(provider, response):
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.update(url=url, body=json, headers=headers)
        return FakeResp(response)

    ai.requests.post = fake_post
    cfg = {"enabled": True, "provider": provider, "model": "m", "key": "k", "max_tokens": 100}
    out = ai.ask("sys", [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"},
                         {"role": "user", "content": "q2"}], cfg)
    return out, calls


def test_providers():
    out, c = run("claude", {"content": [{"type": "text", "text": "클로드"}]})
    assert out == "클로드" and c["headers"]["x-api-key"] == "k" and c["body"]["system"] == "sys"
    out, c = run("openai", {"choices": [{"message": {"content": "지피티"}}]})
    assert out == "지피티" and c["body"]["messages"][0]["role"] == "system"
    out, c = run("gemini", {"candidates": [{"content": {"parts": [{"text": "제미나이"}]}}]})
    assert out == "제미나이" and c["body"]["contents"][1]["role"] == "model"
    ai.requests.post = lambda *a, **k: FakeResp({"error": {"message": "invalid key"}}, 401)
    try:
        ai.ask("s", [{"role": "user", "content": "q"}], {"provider": "openai", "model": "m", "key": "k", "max_tokens": 10})
        raise AssertionError
    except ai.AIError as e:
        assert "invalid key" in str(e)
    print("AI 형식 점검 통과")


if __name__ == "__main__":
    test_providers()
