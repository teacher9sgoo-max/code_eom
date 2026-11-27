"""채점 전용 HTTP 서버 (docker-compose 의 judge 컨테이너에서 실행).

POST /judge  {"code", "tests":[{"input","output"}], "time_limit", "memory_limit"}
POST /run    {"code", "input", "time_limit", "memory_limit"}
GET  /health
요청 헤더 X-Judge-Token 이 JUDGE_TOKEN 환경변수와 일치해야 한다.
"""
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from judge import runner  # noqa: E402

TOKEN = os.environ.get("JUDGE_TOKEN", "")
WORKERS = int(os.environ.get("JUDGE_WORKERS", "4"))
MAX_BODY = 2 * 1024 * 1024
slots = threading.BoundedSemaphore(WORKERS)


class Handler(BaseHTTPRequestHandler):
    server_version = "pyjudge"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "workers": WORKERS})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not TOKEN or not hmac.compare_digest(self.headers.get("X-Judge-Token", ""), TOKEN):
            return self._send(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            return self._send(413, {"error": "too large"})
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            return self._send(400, {"error": "bad json"})
        tl = min(float(data.get("time_limit", 2)), 10.0)
        mem = min(int(data.get("memory_limit", 256)), 512)
        code = str(data.get("code", ""))
        with slots:
            if self.path == "/judge":
                result = runner.judge(code, data.get("tests", []), tl, mem)
            elif self.path == "/run":
                result = runner.run_custom(code, str(data.get("input", "")), tl, mem)
            else:
                return self._send(404, {"error": "not found"})
        self._send(200, result)


if __name__ == "__main__":
    if not TOKEN:
        print("JUDGE_TOKEN 환경변수가 필요합니다.", file=sys.stderr)
        sys.exit(1)
    port = int(os.environ.get("JUDGE_PORT", "9000"))
    print(f"judge server on :{port} (workers={WORKERS})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
