"""채점 요청 처리: 백그라운드 스레드에서 채점기(원격 컨테이너 또는 로컬)를 호출하고 결과를 DB에 저장한다."""
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from flask import current_app

from judge import runner

from .extensions import db
from .models import Problem, Submission

log = logging.getLogger(__name__)
_executor = None
_lock = threading.Lock()


def _pool(app):
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=app.config["JUDGE_CONCURRENCY"],
                                           thread_name_prefix="judge")
    return _executor


def _call(app, path, payload, timeout):
    url = app.config.get("JUDGE_URL")
    if url:
        r = requests.post(url.rstrip("/") + path, json=payload, timeout=timeout,
                          headers={"X-Judge-Token": app.config["JUDGE_TOKEN"]})
        r.raise_for_status()
        return r.json()
    if path == "/judge":
        return runner.judge(payload["code"], payload["tests"], payload["time_limit"], payload["memory_limit"])
    return runner.run_custom(payload["code"], payload["input"], payload["time_limit"], payload["memory_limit"])


def run_custom(code, stdin, problem):
    app = current_app._get_current_object()
    payload = {"code": code, "input": stdin, "time_limit": problem.time_limit, "memory_limit": problem.memory_limit}
    try:
        return _call(app, "/run", payload, timeout=problem.time_limit + 15)
    except Exception as e:  # noqa: BLE001
        log.exception("run failed")
        return {"status": "SE", "output": "", "error": f"채점 서버에 연결할 수 없습니다: {e}", "time": 0}


def _judge_job(app, submission_id):
    with app.app_context():
        sub = db.session.get(Submission, submission_id)
        if sub is None:
            return
        problem = db.session.get(Problem, sub.problem_id)
        tests = [{"input": t.input, "output": t.output} for t in problem.testcases]
        sub.status = "RUNNING"
        db.session.commit()
        payload = {"code": sub.code, "tests": tests, "time_limit": problem.time_limit,
                   "memory_limit": problem.memory_limit}
        try:
            res = _call(app, "/judge", payload, timeout=len(tests) * (problem.time_limit + 2) + 30)
        except Exception as e:  # noqa: BLE001
            log.exception("judge failed")
            res = {"status": "SE", "passed": 0, "total": len(tests), "max_time": 0, "cases": [],
                   "compile_error": f"채점 서버 오류: {e}"}
        sub.status = res.get("status", "SE")
        sub.passed = res.get("passed", 0)
        sub.total = res.get("total", len(tests))
        sub.max_time = res.get("max_time", 0)
        sub.result_json = json.dumps(res, ensure_ascii=False)
        sub.judged_at = datetime.now()
        db.session.commit()


def enqueue(submission_id):
    app = current_app._get_current_object()
    _pool(app).submit(_judge_job, app, submission_id)


def requeue_pending(app):
    """서버 재시작 시 채점되지 않은 제출을 다시 채점한다."""
    with app.app_context():
        ids = [s.id for s in Submission.query.filter(Submission.status.in_(["PENDING", "RUNNING"])).all()]
    for sid in ids:
        _pool(app).submit(_judge_job, app, sid)
