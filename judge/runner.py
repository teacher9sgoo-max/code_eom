"""학생 코드 실행·채점 모듈.

웹 서버(로컬 모드)와 채점 컨테이너(judge/server.py)가 함께 사용한다.
- 프로세스마다 CPU 시간, 메모리(주소 공간), 출력 파일 크기, 코어 덤프를 제한한다.
- 루트 권한으로 실행 중이고 RUNNER_UID 가 지정되어 있으면 학생 코드를 해당 UID로 낮춰 실행한다.
- 실제 격리(네트워크 차단, 읽기 전용 파일 시스템)는 docker-compose 의 judge 서비스가 담당한다.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import time

PYTHON = os.environ.get("RUNNER_PYTHON", sys.executable)
RUNNER_UID = os.environ.get("RUNNER_UID")
RUNNER_GID = os.environ.get("RUNNER_GID", RUNNER_UID)
MAX_SHOW = 2000  # 결과 화면에 보여줄 최대 글자 수

# 학생 코드 실행 전에 감사 훅(audit hook)을 설치해 네트워크·외부 프로세스·ctypes 사용을 막는다.
# 컨테이너 격리와 함께 쓰는 보조 방어선이며, 한 번 설치된 훅은 파이썬 코드로 제거할 수 없다.
BOOTSTRAP = r"""
import sys
_BLOCK = ("socket.__new__", "socket.connect", "socket.bind", "subprocess.Popen", "os.system", "os.exec",
          "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "ctypes.dlopen", "ctypes.dlsym", "pty.spawn")
def _guard(event, args):
    if event in _BLOCK or (event == "import" and args and str(args[0]).split(".")[0] in ("ctypes", "_ctypes")):
        raise PermissionError("채점 환경에서는 사용할 수 없는 기능입니다: " + event)
sys.addaudithook(_guard)
del _guard
with open("main.py", encoding="utf-8") as _f:
    _code = compile(_f.read(), "main.py", "exec")
del _f
exec(_code, {"__name__": "__main__", "__file__": "main.py", "__builtins__": __builtins__})
"""

STATUS_LABELS = {
    "AC": "정답",
    "WA": "오답",
    "TLE": "시간 초과",
    "MLE": "메모리 초과",
    "RE": "런타임 에러",
    "CE": "문법 오류",
    "OLE": "출력 초과",
    "SE": "채점 오류",
    "PENDING": "채점 대기",
    "RUNNING": "채점 중",
}
# 여러 케이스가 틀렸을 때 대표 결과를 정하는 우선순위
STATUS_PRIORITY = ["CE", "SE", "RE", "MLE", "TLE", "OLE", "WA", "AC"]


def _clip(text: str, limit: int = MAX_SHOW) -> str:
    if text is None:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n...(이하 생략)"
    return text


def normalize(text: str) -> str:
    """줄 끝 공백과 마지막 빈 줄 차이는 무시한다."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip() for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _clean_error(err: str, workdir: str) -> str:
    err = err.replace(workdir + os.sep, "").replace(workdir, "")
    # 실행 도우미(BOOTSTRAP) 프레임은 학생에게 보이지 않게 제거
    out, skip = [], False
    for ln in err.split("\n"):
        if ln.startswith('  File "<string>"'):
            skip = True
            continue
        if skip and ln.startswith("    "):
            continue
        skip = False
        out.append(ln)
    err = "\n".join(out)
    err = re.sub(r'File ".*?main\.py"', 'File "main.py"', err)
    return err.strip()


def check_syntax(code: str) -> str | None:
    """문법 오류가 있으면 학생이 읽기 쉬운 메시지를 반환한다. compile 은 코드를 실행하지 않는다."""
    try:
        compile(code, "main.py", "exec")
    except SyntaxError as e:  # IndentationError, TabError 포함
        line = (e.text or "").rstrip("\n")
        pointer = ""
        if e.offset and line:
            pointer = "\n    " + " " * (max(e.offset, 1) - 1) + "^"
        return f'File "main.py", line {e.lineno}\n    {line}{pointer}\n{type(e).__name__}: {e.msg}'
    except (ValueError, MemoryError, RecursionError) as e:
        return f"{type(e).__name__}: {e}"
    return None


def run_once(code: str, stdin: str, time_limit: float = 2.0, memory_mb: int = 256,
             output_limit_kb: int = 1024) -> dict:
    """코드를 한 번 실행하고 {status, output, error, time} 을 반환한다(정답 비교는 하지 않음)."""
    import resource

    workdir = tempfile.mkdtemp(prefix="run_")
    try:
        src = os.path.join(workdir, "main.py")
        with open(src, "w", encoding="utf-8") as f:
            f.write(code)
        os.chmod(workdir, 0o755)
        os.chmod(src, 0o644)
        out_path = os.path.join(workdir, ".out")
        err_path = os.path.join(workdir, ".err")
        out_f = open(out_path, "wb")
        err_f = open(err_path, "wb")

        mem_bytes = int(memory_mb) * 1024 * 1024 + 64 * 1024 * 1024  # 인터프리터 기본 사용량 여유
        cpu_sec = int(time_limit) + 1
        fsize = int(output_limit_kb) * 1024
        drop_uid = RUNNER_UID is not None and os.geteuid() == 0

        def preexec():
            os.setsid()
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec + 1))
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            if drop_uid:
                os.setgroups([])
                os.setgid(int(RUNNER_GID))
                os.setuid(int(RUNNER_UID))

        env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
               "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": workdir, "LANG": "C.UTF-8"}
        start = time.monotonic()
        status = None
        try:
            proc = subprocess.Popen([PYTHON, "-I", "-B", "-c", BOOTSTRAP], stdin=subprocess.PIPE,
                                    stdout=out_f, stderr=err_f, cwd=workdir, env=env,
                                    preexec_fn=preexec, close_fds=True)
        except Exception as e:  # 실행기 자체 문제
            out_f.close(); err_f.close()
            return {"status": "SE", "output": "", "error": f"실행 실패: {e}", "time": 0}
        try:
            proc.communicate((stdin or "").encode("utf-8"), timeout=time_limit + 0.5)
        except subprocess.TimeoutExpired:
            status = "TLE"
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        except BrokenPipeError:
            proc.wait()
        elapsed = time.monotonic() - start
        out_f.close(); err_f.close()
        # 남은 자식 프로세스 정리
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

        with open(out_path, "rb") as f:
            output = f.read(fsize + 1).decode("utf-8", errors="replace")
        with open(err_path, "rb") as f:
            error = f.read(64 * 1024).decode("utf-8", errors="replace")
        error = _clean_error(error, workdir)
        rc = proc.returncode

        if status is None:
            if rc == -signal.SIGXFSZ or len(output.encode("utf-8")) >= fsize or "[Errno 27]" in error:
                status = "OLE"
            elif rc in (-signal.SIGXCPU, -signal.SIGKILL) and elapsed >= time_limit * 0.9:
                status = "TLE"
            elif elapsed > time_limit:
                status = "TLE"
            elif "MemoryError" in error:
                status = "MLE"
            elif rc != 0:
                status = "RE"
            else:
                status = "OK"
        return {"status": status, "output": output, "error": error, "time": round(elapsed, 3)}
    finally:
        for name in (".out", ".err", "main.py"):
            try:
                os.unlink(os.path.join(workdir, name))
            except OSError:
                pass
        try:
            for name in os.listdir(workdir):
                p = os.path.join(workdir, name)
                if os.path.isfile(p):
                    os.unlink(p)
            os.rmdir(workdir)
        except OSError:
            pass


def judge(code: str, tests: list[dict], time_limit: float = 2.0, memory_mb: int = 256) -> dict:
    """tests: [{"input": str, "output": str}] → 채점 결과 dict."""
    syntax = check_syntax(code)
    if syntax:
        return {"status": "CE", "passed": 0, "total": len(tests), "max_time": 0,
                "compile_error": syntax, "cases": []}
    cases = []
    passed = 0
    max_time = 0.0
    tle_count = 0
    for i, t in enumerate(tests):
        if tle_count >= 3:  # 시간 초과가 반복되면 나머지는 건너뛴다
            cases.append({"no": i + 1, "status": "TLE", "time": 0, "output": "", "error": "앞선 케이스에서 시간 초과가 반복되어 실행하지 않았습니다."})
            continue
        r = run_once(code, t.get("input", ""), time_limit, memory_mb)
        st = r["status"]
        if st == "OK":
            st = "AC" if normalize(r["output"]) == normalize(t.get("output", "")) else "WA"
        if st == "AC":
            passed += 1
        if st == "TLE":
            tle_count += 1
        max_time = max(max_time, r["time"])
        cases.append({"no": i + 1, "status": st, "time": r["time"],
                      "output": _clip(r["output"]), "error": _clip(r["error"], 1500)})
    overall = "AC"
    for st in STATUS_PRIORITY:
        if any(c["status"] == st for c in cases):
            overall = st
            break
    if not tests:
        overall = "SE"
    return {"status": overall, "passed": passed, "total": len(tests),
            "max_time": round(max_time, 3), "cases": cases}


def run_custom(code: str, stdin: str, time_limit: float = 2.0, memory_mb: int = 256) -> dict:
    """학생이 직접 입력값을 넣어 실행해 보는 기능."""
    syntax = check_syntax(code)
    if syntax:
        return {"status": "CE", "output": "", "error": syntax, "time": 0}
    r = run_once(code, stdin, time_limit, memory_mb)
    r["output"] = _clip(r["output"], 10000)
    r["error"] = _clip(r["error"], 3000)
    return r
