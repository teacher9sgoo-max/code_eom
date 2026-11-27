from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from judge.runner import STATUS_LABELS

from . import ai
from .extensions import db
from .judge_client import enqueue, run_custom
from .models import AILog, Problem, Quiz, QuizAttempt, Submission, Unit
from .utils import get_flag, get_setting, render_md

bp = Blueprint("student", __name__)
MAX_CODE = 64 * 1024


def user_problem_sets(user_id):
    solved = {pid for (pid,) in db.session.query(Submission.problem_id)
              .filter_by(user_id=user_id, status="AC").distinct()}
    tried = {pid for (pid,) in db.session.query(Submission.problem_id)
             .filter_by(user_id=user_id).distinct()}
    return solved, tried - solved


def visible_problems():
    q = Problem.query
    if not current_user.is_admin:
        q = q.filter_by(published=True)
    return q


def get_problem_or_404(number):
    p = visible_problems().filter_by(number=number).first()
    if p is None:
        abort(404)
    return p


def quiz_state(user_id, problem):
    """퀴즈별 {quiz_id: {"correct": bool, "tries": int}}"""
    ids = [q.id for q in problem.quizzes]
    state = {qid: {"correct": False, "tries": 0} for qid in ids}
    if not ids:
        return state
    for a in QuizAttempt.query.filter(QuizAttempt.user_id == user_id, QuizAttempt.quiz_id.in_(ids)).all():
        st = state[a.quiz_id]
        st["tries"] += 1
        st["correct"] = st["correct"] or a.correct
    return state


def submission_view(sub):
    """학생에게 보여줄 채점 결과 (비공개 케이스는 설정에 따라 입력/정답 숨김)."""
    res = sub.result
    show_hidden = get_flag("show_hidden_cases") or current_user.is_admin
    tests = sub.problem.testcases
    cases = []
    for c in res.get("cases", []):
        idx = c.get("no", 0) - 1
        t = tests[idx] if 0 <= idx < len(tests) else None
        visible = t is not None and (t.is_sample or show_hidden)
        cases.append({
            "no": c.get("no"), "status": c.get("status"), "label": STATUS_LABELS.get(c.get("status"), c.get("status")),
            "time": c.get("time"), "sample": bool(t and t.is_sample), "visible": visible,
            "input": t.input if visible else None, "expected": t.output if visible else None,
            "output": c.get("output") if visible else None, "error": c.get("error", ""),
        })
    return {
        "id": sub.id, "status": sub.status, "label": STATUS_LABELS.get(sub.status, sub.status),
        "passed": sub.passed, "total": sub.total, "max_time": sub.max_time,
        "compile_error": res.get("compile_error"), "cases": cases,
        "done": sub.status not in ("PENDING", "RUNNING"),
        "created_at": sub.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "url": url_for("student.submission_detail", sid=sub.id),
    }


# ------------------------------------------------------------------ 페이지
@bp.route("/")
@login_required
def home():
    if current_user.is_admin and request.args.get("preview") != "1":
        from flask import redirect
        return redirect(url_for("admin.dashboard"))
    solved, tried = user_problem_sets(current_user.id)
    units = Unit.query.order_by(Unit.order).all()
    unit_stats = []
    next_problem = None
    for u in units:
        probs = [p for p in u.problems if p.published]
        if not probs:
            continue
        done = sum(1 for p in probs if p.id in solved)
        unit_stats.append({"unit": u, "total": len(probs), "solved": done})
        if next_problem is None:
            next_problem = next((p for p in probs if p.id not in solved), None)
    total = sum(s["total"] for s in unit_stats)
    recent = (Submission.query.filter_by(user_id=current_user.id)
              .order_by(Submission.id.desc()).limit(8).all())
    sub_count = Submission.query.filter_by(user_id=current_user.id).count()
    ac_count = Submission.query.filter_by(user_id=current_user.id, status="AC").count()
    return render_template("student/home.html", unit_stats=unit_stats, solved=len(solved), total=total,
                           tried=len(tried), recent=recent, next_problem=next_problem,
                           sub_count=sub_count, ac_count=ac_count,
                           announcement=get_setting("announcement"))


@bp.route("/problems")
@login_required
def problems():
    solved, tried = user_problem_sets(current_user.id)
    q = request.args.get("q", "").strip()
    level = request.args.get("level", type=int)
    status = request.args.get("status", "")
    units = Unit.query.order_by(Unit.order).all()
    # 문제별 정답률
    stats = dict(db.session.query(Submission.problem_id, func.count(func.distinct(Submission.user_id)))
                 .filter(Submission.status == "AC").group_by(Submission.problem_id).all())
    groups = []
    for u in units:
        items = []
        for p in u.problems:
            if not p.published and not current_user.is_admin:
                continue
            if q and q.lower() not in p.title.lower() and q != str(p.number):
                continue
            if level and p.level != level:
                continue
            st = "solved" if p.id in solved else "tried" if p.id in tried else "new"
            if status and st != status:
                continue
            items.append({"p": p, "state": st, "solvers": stats.get(p.id, 0)})
        if items:
            groups.append({"unit": u, "rows": items, "solved": sum(1 for i in items if i["state"] == "solved")})
    return render_template("student/problems.html", groups=groups, q=q, level=level, status=status)


@bp.route("/problems/<int:number>")
@login_required
def problem(number):
    p = get_problem_or_404(number)
    state = quiz_state(current_user.id, p)
    last = (Submission.query.filter_by(user_id=current_user.id, problem_id=p.id)
            .order_by(Submission.id.desc()).first())
    solved = Submission.query.filter_by(user_id=current_user.id, problem_id=p.id, status="AC").first() is not None
    prev_p = visible_problems().filter(Problem.number < p.number).order_by(Problem.number.desc()).first()
    next_p = visible_problems().filter(Problem.number > p.number).order_by(Problem.number).first()
    my_subs = (Submission.query.filter_by(user_id=current_user.id, problem_id=p.id)
               .order_by(Submission.id.desc()).limit(10).all())
    quiz_all_done = all(s["correct"] for s in state.values())
    return render_template(
        "student/problem.html", p=p, quiz_state=state, last=last, solved=solved,
        prev_p=prev_p, next_p=next_p, my_subs=my_subs, quiz_all_done=quiz_all_done,
        require_quiz=get_flag("require_quiz"), allow_custom_run=get_flag("allow_custom_run"),
        ai_enabled=get_flag("ai_enabled"),
        last_view=submission_view(last) if last and last.status not in ("PENDING", "RUNNING") else None,
    )


@bp.route("/submissions")
@login_required
def my_submissions():
    page = request.args.get("page", 1, type=int)
    q = Submission.query.filter_by(user_id=current_user.id).order_by(Submission.id.desc())
    pg = q.paginate(page=page, per_page=30, error_out=False)
    return render_template("student/submissions.html", pg=pg)


@bp.route("/submissions/<int:sid>")
@login_required
def submission_detail(sid):
    sub = db.get_or_404(Submission, sid)
    if sub.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    return render_template("student/submission.html", sub=sub, view=submission_view(sub),
                           ai_enabled=get_flag("ai_enabled"))


# ------------------------------------------------------------------ API
def _json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


@bp.post("/api/quiz/<int:qid>")
@login_required
def api_quiz(qid):
    quiz = db.get_or_404(Quiz, qid)
    if not quiz.problem.published and not current_user.is_admin:
        abort(404)
    answer = str(_json().get("answer", ""))[:500]
    correct = quiz.check(answer)
    db.session.add(QuizAttempt(user_id=current_user.id, quiz_id=quiz.id, answer=answer, correct=correct))
    db.session.commit()
    tries = QuizAttempt.query.filter_by(user_id=current_user.id, quiz_id=quiz.id).count()
    resp = {"correct": correct, "tries": tries}
    if correct or tries >= 2:
        resp["explanation"] = str(render_md(quiz.explanation))
        if quiz.kind == "choice":
            try:
                resp["answer"] = int(quiz.answer)
            except ValueError:
                pass
        else:
            resp["answer_text"] = quiz.answer.split("|")[0]
    state = quiz_state(current_user.id, quiz.problem)
    resp["all_done"] = all(s["correct"] for s in state.values())
    return jsonify(resp)


@bp.post("/api/run/<int:number>")
@login_required
def api_run(number):
    p = get_problem_or_404(number)
    if not get_flag("allow_custom_run") and not current_user.is_admin:
        abort(403)
    data = _json()
    code = str(data.get("code", ""))[:MAX_CODE]
    stdin = str(data.get("input", ""))[:64 * 1024]
    res = run_custom(code, stdin, p)
    res["label"] = STATUS_LABELS.get(res.get("status"), "실행 완료") if res.get("status") != "OK" else "실행 완료"
    return jsonify(res)


@bp.post("/api/submit/<int:number>")
@login_required
def api_submit(number):
    p = get_problem_or_404(number)
    code = str(_json().get("code", ""))
    if not code.strip():
        return jsonify({"error": "코드를 입력하세요."}), 400
    if len(code) > MAX_CODE:
        return jsonify({"error": "코드가 너무 깁니다 (최대 64KB)."}), 400
    if get_flag("require_quiz") and not current_user.is_admin:
        if not all(s["correct"] for s in quiz_state(current_user.id, p).values()):
            return jsonify({"error": "개념 퀴즈를 모두 맞힌 뒤 제출할 수 있습니다."}), 403
    pending = Submission.query.filter(Submission.user_id == current_user.id,
                                      Submission.status.in_(["PENDING", "RUNNING"])).count()
    if pending >= 2:
        return jsonify({"error": "이전 제출을 채점 중입니다. 잠시 후 다시 제출하세요."}), 429
    last = Submission.query.filter_by(user_id=current_user.id).order_by(Submission.id.desc()).first()
    if last and (datetime.now() - last.created_at).total_seconds() < 3:
        return jsonify({"error": "너무 빠르게 제출하고 있습니다. 3초 후 다시 시도하세요."}), 429
    sub = Submission(user_id=current_user.id, problem_id=p.id, code=code, status="PENDING",
                     total=len(p.testcases))
    db.session.add(sub)
    db.session.commit()
    enqueue(sub.id)
    return jsonify({"id": sub.id})


@bp.get("/api/submission/<int:sid>")
@login_required
def api_submission(sid):
    sub = db.get_or_404(Submission, sid)
    if sub.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    data = submission_view(sub)
    if request.args.get("code") == "1":
        data["code"] = sub.code
    return jsonify(data)


def _ai_usage_today(user_id):
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return AILog.query.filter(AILog.user_id == user_id, AILog.created_at >= start, AILog.ok.is_(True)).count()


@bp.post("/api/ai")
@login_required
def api_ai():
    if not get_flag("ai_enabled"):
        return jsonify({"error": "AI 도우미가 꺼져 있습니다."}), 403
    data = _json()
    limit = int(get_setting("ai_daily_limit") or 0)
    used = _ai_usage_today(current_user.id)
    if limit and used >= limit and not current_user.is_admin:
        return jsonify({"error": f"오늘 사용할 수 있는 AI 질문 횟수({limit}회)를 모두 사용했습니다."}), 429
    # 같은 학생의 연속 요청 제한
    recent = AILog.query.filter(AILog.user_id == current_user.id,
                                AILog.created_at >= datetime.now() - timedelta(seconds=3)).count()
    if recent:
        return jsonify({"error": "잠시 후 다시 질문해 주세요."}), 429

    p = get_problem_or_404(int(data.get("problem", 0))) if data.get("problem") else None
    kind = data.get("kind", "chat")
    message = str(data.get("message", "")).strip()[:2000]
    history = []
    for m in (data.get("history") or [])[-8:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            history.append({"role": m["role"], "content": str(m.get("content", ""))[:3000]})

    context = ""
    if kind == "quiz":
        quiz = db.get_or_404(Quiz, int(data.get("quiz_id", 0)))
        opts = "\n".join(f"{i + 1}) {o}" for i, o in enumerate(quiz.options))
        given = str(data.get("quiz_answer", ""))
        if quiz.kind == "choice" and given.isdigit() and int(given) < len(quiz.options):
            given = f"{int(given) + 1}) {quiz.options[int(given)]}"
        context = (f"[개념 퀴즈]\n{quiz.question}\n{opts}\n[학생이 고른 답] {given}\n"
                   f"[정답] {quiz.answer if quiz.kind == 'short' else int(quiz.answer) + 1}\n"
                   "학생이 이 퀴즈를 틀렸습니다. 정답 번호를 바로 말하지 말고, 학생의 답이 왜 틀렸는지와 "
                   "관련 개념을 설명해 스스로 다시 풀 수 있게 도와주세요.")
        if not message:
            message = "이 퀴즈를 왜 틀렸는지 알려주세요."
    elif kind == "submission":
        sub = db.get_or_404(Submission, int(data.get("submission_id", 0)))
        if sub.user_id != current_user.id and not current_user.is_admin:
            abort(403)
        view = submission_view(sub)
        failed = [c for c in view["cases"] if c["status"] != "AC"][:2]
        context = f"[학생 제출 코드]\n```python\n{sub.code[:6000]}\n```\n[채점 결과] {view['label']} ({sub.passed}/{sub.total})\n"
        if view.get("compile_error"):
            context += f"[문법 오류]\n{view['compile_error']}\n"
        for c in failed:
            context += f"[{c['no']}번 케이스: {c['label']}]\n"
            if c["visible"]:
                context += f"입력:\n{(c['input'] or '')[:800]}\n기대 출력:\n{(c['expected'] or '')[:800]}\n학생 출력:\n{(c['output'] or '')[:800]}\n"
            if c["error"]:
                context += f"오류 메시지:\n{c['error'][:800]}\n"
        if not message:
            message = "제 코드가 왜 틀렸는지 힌트를 주세요."
    else:
        code = str(data.get("code", ""))[:6000]
        if code.strip():
            context = f"[학생이 현재 작성 중인 코드]\n```python\n{code}\n```"
        if not message:
            return jsonify({"error": "질문을 입력하세요."}), 400

    user_msg = (context + "\n\n[학생 질문]\n" + message) if context else message
    messages = history + [{"role": "user", "content": user_msg}]
    cfg = ai.current_config()
    log = AILog(user_id=current_user.id, problem_id=p.id if p else None, kind=kind, question=message,
                provider=cfg["provider"], model=cfg["model"] or "")
    try:
        answer = ai.ask(ai.system_prompt(p), messages, cfg)
        log.answer = answer
    except ai.AIError as e:
        log.ok = False
        log.answer = str(e)
        db.session.add(log)
        db.session.commit()
        msg = str(e) if current_user.is_admin else "AI 도우미에 일시적인 문제가 있습니다. 선생님께 알려 주세요."
        return jsonify({"error": msg}), 502
    db.session.add(log)
    db.session.commit()
    return jsonify({"answer": answer, "html": str(render_md(answer)),
                    "remaining": (limit - used - 1) if limit else None, "question": message})
