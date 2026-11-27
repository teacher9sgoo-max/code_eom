"""전체 기능 점검 테스트:  python -m pytest -q  (또는 python tests/test_smoke.py)"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["INSTANCE_PATH"] = tempfile.mkdtemp()
os.environ.setdefault("PYJUDGE_NO_REQUEUE", "1")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Problem, Submission  # noqa: E402


def wait_judged(app, sid):
    for _ in range(100):
        with app.app_context():
            s = db.session.get(Submission, sid)
            if s.status not in ("PENDING", "RUNNING"):
                return s.status
        time.sleep(0.1)
    raise AssertionError("채점 시간 초과")


def test_all():
    app = create_app({"WTF_CSRF_ENABLED": False})
    c = app.test_client()
    with app.app_context():
        assert Problem.query.count() >= 100

    # 관리자 로그인 + 첫 비밀번호 변경
    r = c.post("/login", data={"student_no": "admin", "password": "admin1234"})
    assert r.status_code == 302
    assert c.get("/admin/").location.endswith("/account/password")
    r = c.post("/account/password", data={"current": "admin1234", "new": "teacher!234", "confirm": "teacher!234"})
    assert r.status_code == 302

    for url in ["/admin/", "/admin/students", "/admin/problems", "/admin/units", "/admin/submissions",
                "/admin/stats", "/admin/ai", "/admin/ai/logs", "/admin/settings", "/admin/problems/new",
                "/admin/problems/import", "/admin/problems/1/edit", "/problems", "/problems/1001"]:
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code)
    assert c.get("/admin/problems/export").status_code == 200
    assert c.get("/admin/students/export.csv").status_code == 200

    # 학생 등록 (단일 + 일괄)
    c.post("/admin/students", data={"action": "add", "student_no": "30101", "name": "김민준"})
    c.post("/admin/students", data={"action": "bulk", "bulk": "학번,이름\n30102,이서연\n30103\t박지호\n30101,중복"})
    page = c.get("/admin/students").get_data(as_text=True)
    assert "김민준" in page and "이서연" in page and "박지호" in page

    # 모범 답안 검증
    v = c.post("/admin/problems/1/verify", json={}).get_json()
    assert v["status"] == "AC", v

    # 학생 로그인
    s = app.test_client()
    s.post("/login", data={"student_no": "30101", "password": "30101"})
    s.post("/account/password", data={"current": "30101", "new": "pw1234", "confirm": "pw1234"})
    assert s.get("/").status_code == 200
    assert s.get("/problems").status_code == 200
    assert s.get("/problems/1018").status_code == 200
    assert s.get("/admin/").status_code == 403

    with app.app_context():
        p = Problem.query.filter_by(number=1018).first()
        qid = p.quizzes[0].id
        ans = p.quizzes[0].answer
    r = s.post(f"/api/quiz/{qid}", json={"answer": "오답"}).get_json()
    assert r["correct"] is False and "explanation" not in r
    r = s.post(f"/api/quiz/{qid}", json={"answer": ans}).get_json()
    assert r["correct"] is True and r["all_done"] is True

    # 직접 실행
    r = s.post("/api/run/1018", json={"code": "a,b=map(int,input().split())\nprint(a+b)", "input": "2 3"}).get_json()
    assert r["status"] == "OK" and r["output"].strip() == "5", r

    # 정답 제출
    sid = s.post("/api/submit/1018", json={"code": "a,b=map(int,input().split())\nprint(a+b)"}).get_json()["id"]
    assert wait_judged(app, sid) == "AC"
    time.sleep(3.1)
    # 오답 제출 → 실패 케이스 확인
    sid = s.post("/api/submit/1018", json={"code": "a,b=map(int,input().split())\nprint(a-b)"}).get_json()["id"]
    assert wait_judged(app, sid) == "WA"
    v = s.get(f"/api/submission/{sid}").get_json()
    assert any(cs["status"] == "WA" and cs["expected"] is not None for cs in v["cases"])
    assert s.get(f"/submissions/{sid}").status_code == 200
    time.sleep(3.1)
    # 문법 오류
    sid = s.post("/api/submit/1018", json={"code": "print(1"}).get_json()["id"]
    assert wait_judged(app, sid) == "CE"

    # AI: 꺼져 있으면 403
    assert s.post("/api/ai", json={"problem": 1018, "message": "hi"}).status_code == 403
    c.post("/admin/ai", data={"ai_enabled": "1", "ai_provider": "claude", "ai_daily_limit": "5", "ai_max_tokens": "800"})
    r = s.post("/api/ai", json={"problem": 1018, "kind": "chat", "message": "힌트 주세요"})
    assert r.status_code == 502  # 키가 없으므로 오류 메시지
    assert c.get("/admin/ai").status_code == 200

    # 관리자 화면 데이터가 있는 상태에서 다시 확인
    for url in ["/admin/", "/admin/stats", "/admin/submissions?status=WA", "/admin/students/2", "/admin/ai/logs"]:
        assert c.get(url).status_code == 200, url
    # 다른 학생 제출 열람 불가
    t = app.test_client()
    t.post("/login", data={"student_no": "30102", "password": "30102"})
    t.post("/account/password", data={"current": "30102", "new": "pw5678", "confirm": "pw5678"})
    assert t.get(f"/submissions/{sid}").status_code == 403
    print("모든 점검 통과")


if __name__ == "__main__":
    test_all()
