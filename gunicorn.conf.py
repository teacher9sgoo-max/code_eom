# 채점 작업 큐가 프로세스 안에 있으므로 워커는 1개, 스레드로 동시 접속을 처리한다.
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8000")
workers = 1
threads = int(os.environ.get("WEB_THREADS", "16"))
timeout = 120
accesslog = "-"
forwarded_allow_ips = "*"


def post_worker_init(worker):
    """서버 재시작 전에 채점되지 못한 제출을 다시 채점한다."""
    from app.judge_client import requeue_pending
    requeue_pending(worker.wsgi)
