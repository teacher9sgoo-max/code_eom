import csv
import io
import json
import re
from datetime import datetime, timedelta

from flask import (Blueprint, Response, abort, flash, jsonify, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func

from . import ai
from .extensions import db
from .judge_client import enqueue
from .models import AILog, Problem, Quiz, QuizAttempt, Submission, TestCase, Unit, User
from .seed import apply_problem_data, export_all, load_problem_data, problem_to_dict
from .utils import admin_required, decrypt, encrypt, get_setting, mask_key, set_setting

bp = Blueprint("admin", __name__)


@bp.before_request
@login_required
@admin_required
def _guard():
    pass


def students_q():
    return User.query.filter_by(is_admin=False)


def solved_counts():
    rows = (db.session.query(Submission.user_id, func.count(func.distinct(Submission.problem_id)))
            .filter(Submission.status == "AC").group_by(Submission.user_id).all())
    return dict(rows)


# ------------------------------------------------------------------ 대시보드
@bp.route("/")
def dashboard():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    n_students = students_q().count()
    n_problems = Problem.query.count()
    n_published = Problem.query.filter_by(published=True).count()
    subs_today = Submission.query.filter(Submission.created_at >= today).count()
    ac_today = Submission.query.filter(Submission.created_at >= today, Submission.status == "AC").count()
    active_7d = (db.session.query(func.count(func.distinct(Submission.user_id)))
                 .filter(Submission.created_at >= today - timedelta(days=6)).scalar())
    ai_today = AILog.query.filter(AILog.created_at >= today).count()

    # 최근 14일 제출 추이
    start = today - timedelta(days=13)
    daily = {(start + timedelta(days=i)).date(): [0, 0] for i in range(14)}
    for created, status in (db.session.query(Submission.created_at, Submission.status)
                            .filter(Submission.created_at >= start).all()):
        d = created.date()
        if d in daily:
            daily[d][0] += 1
            if status == "AC":
                daily[d][1] += 1
    daily_list = [{"label": d.strftime("%m/%d"), "total": v[0], "ac": v[1]} for d, v in daily.items()]
    daily_max = max([d["total"] for d in daily_list] + [1])

    status_dist = dict(db.session.query(Submission.status, func.count()).group_by(Submission.status).all())

    # 어려운 문제: 시도 학생 3명 이상 중 해결 비율이 낮은 순
    tried = dict(db.session.query(Submission.problem_id, func.count(func.distinct(Submission.user_id)))
                 .group_by(Submission.problem_id).all())
    solved = dict(db.session.query(Submission.problem_id, func.count(func.distinct(Submission.user_id)))
                  .filter(Submission.status == "AC").group_by(Submission.problem_id).all())
    hard = []
    for pid, t in tried.items():
        if t >= 3:
            hard.append((solved.get(pid, 0) / t, pid, t, solved.get(pid, 0)))
    hard.sort()
    hard_rows = []
    for rate, pid, t, s in hard[:8]:
        p = db.session.get(Problem, pid)
        if p:
            hard_rows.append({"p": p, "tried": t, "solved": s, "rate": round(rate * 100)})

    counts = solved_counts()
    top = sorted(students_q().all(), key=lambda u: -counts.get(u.id, 0))[:8]
    top_rows = [{"u": u, "solved": counts.get(u.id, 0)} for u in top if counts.get(u.id, 0)]
    recent = Submission.query.order_by(Submission.id.desc()).limit(12).all()
    return render_template("admin/dashboard.html", n_students=n_students, n_problems=n_problems,
                           n_published=n_published, subs_today=subs_today, ac_today=ac_today,
                           active_7d=active_7d, ai_today=ai_today, daily=daily_list, daily_max=daily_max,
                           status_dist=status_dist, hard_rows=hard_rows, top_rows=top_rows, recent=recent,
                           ai_enabled=get_setting("ai_enabled") == "1",
                           ai_provider=ai.PROVIDERS.get(get_setting("ai_provider"), ""))


# ------------------------------------------------------------------ 학생 관리
def _initial_password(student_no):
    return get_setting("default_password") or student_no


def _valid_no(s):
    return bool(re.fullmatch(r"[0-9A-Za-z_\-]{1,40}", s))


@bp.route("/students", methods=["GET", "POST"])
def students():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            sno = request.form.get("student_no", "").strip()
            name = request.form.get("name", "").strip()
            if not _valid_no(sno) or not name:
                flash("학번(영문·숫자)과 이름을 정확히 입력하세요.", "error")
            elif User.query.filter_by(student_no=sno).first():
                flash(f"이미 등록된 학번입니다: {sno}", "error")
            else:
                u = User(student_no=sno, name=name, must_change_password=True)
                u.set_password(_initial_password(sno))
                db.session.add(u)
                db.session.commit()
                flash(f"{sno} {name} 학생을 등록했습니다.", "success")
        elif action == "bulk":
            text = request.form.get("bulk", "")
            f = request.files.get("csv_file")
            if f and f.filename:
                text += "\n" + f.read().decode("utf-8-sig", errors="replace")
            added, skipped, errors = 0, 0, []
            for ln, line in enumerate(text.splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                parts = [x.strip() for x in re.split(r"[,\t]", line) if x.strip()]
                if len(parts) < 2:
                    parts = line.split(None, 1)
                if len(parts) < 2:
                    errors.append(f"{ln}행: 형식 오류"); continue
                sno, name = parts[0], parts[1]
                if sno in ("학번", "student_no"):
                    continue
                if not _valid_no(sno):
                    errors.append(f"{ln}행: 학번 형식 오류 ({sno})"); continue
                if User.query.filter_by(student_no=sno).first():
                    skipped += 1; continue
                u = User(student_no=sno, name=name, must_change_password=True)
                u.set_password(_initial_password(sno))
                db.session.add(u)
                db.session.flush()
                added += 1
            db.session.commit()
            msg = f"{added}명 등록"
            if skipped:
                msg += f", 이미 있는 학번 {skipped}명 건너뜀"
            flash(msg, "success")
            for e in errors[:10]:
                flash(e, "error")
        elif action in ("reset_selected", "delete_selected", "deactivate_selected", "activate_selected"):
            ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
            users = students_q().filter(User.id.in_(ids)).all() if ids else []
            for u in users:
                if action == "reset_selected":
                    u.set_password(_initial_password(u.student_no)); u.must_change_password = True
                elif action == "delete_selected":
                    db.session.delete(u)
                elif action == "deactivate_selected":
                    u.is_active_flag = False
                else:
                    u.is_active_flag = True
            db.session.commit()
            flash(f"{len(users)}명에게 적용했습니다.", "success")
        return redirect(url_for("admin.students", q=request.args.get("q", "")))

    q = request.args.get("q", "").strip()
    query = students_q()
    if q:
        query = query.filter((User.student_no.contains(q)) | (User.name.contains(q)))
    users = query.order_by(User.student_no).all()
    counts = solved_counts()
    sub_counts = dict(db.session.query(Submission.user_id, func.count()).group_by(Submission.user_id).all())
    return render_template("admin/students.html", users=users, counts=counts, sub_counts=sub_counts, q=q,
                           total_problems=Problem.query.filter_by(published=True).count(),
                           default_pw_rule="학번" if not get_setting("default_password") else "관리자 지정 초기 비밀번호")


@bp.route("/students/<int:uid>", methods=["GET", "POST"])
def student_detail(uid):
    u = db.get_or_404(User, uid)
    if u.is_admin:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "update":
            sno = request.form.get("student_no", "").strip()
            name = request.form.get("name", "").strip()
            other = User.query.filter(User.student_no == sno, User.id != u.id).first()
            if not _valid_no(sno) or not name or other:
                flash("학번이 중복되었거나 형식이 올바르지 않습니다.", "error")
            else:
                u.student_no, u.name = sno, name
                db.session.commit()
                flash("저장했습니다.", "success")
        elif action == "reset":
            u.set_password(_initial_password(u.student_no))
            u.must_change_password = True
            db.session.commit()
            flash("비밀번호를 초기화했습니다. 다음 로그인 때 새 비밀번호를 설정합니다.", "success")
        elif action == "toggle":
            u.is_active_flag = not u.is_active_flag
            db.session.commit()
            flash("계정 상태를 변경했습니다.", "success")
        elif action == "delete":
            db.session.delete(u)
            db.session.commit()
            flash("학생을 삭제했습니다.", "success")
            return redirect(url_for("admin.students"))
        return redirect(url_for("admin.student_detail", uid=u.id))

    solved = {pid for (pid,) in db.session.query(Submission.problem_id)
              .filter_by(user_id=u.id, status="AC").distinct()}
    tried = {pid for (pid,) in db.session.query(Submission.problem_id).filter_by(user_id=u.id).distinct()}
    units = []
    for unit in Unit.query.order_by(Unit.order).all():
        probs = [p for p in unit.problems if p.published]
        if probs:
            units.append({"unit": unit, "probs": probs})
    subs = Submission.query.filter_by(user_id=u.id).order_by(Submission.id.desc()).limit(50).all()
    qa = QuizAttempt.query.filter_by(user_id=u.id)
    quiz_total = qa.count()
    quiz_correct = qa.filter_by(correct=True).count()
    ai_logs = AILog.query.filter_by(user_id=u.id).order_by(AILog.id.desc()).limit(20).all()
    return render_template("admin/student_detail.html", u=u, solved=solved, tried=tried, units=units,
                           subs=subs, quiz_total=quiz_total, quiz_correct=quiz_correct, ai_logs=ai_logs)


@bp.route("/students/export.csv")
def students_export():
    counts = solved_counts()
    units = Unit.query.order_by(Unit.order).all()
    ac = {}
    for uid, pid in (db.session.query(Submission.user_id, Submission.problem_id)
                     .filter(Submission.status == "AC").distinct()):
        ac.setdefault(uid, set()).add(pid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["학번", "이름", "해결 문제 수", "제출 수"] + [u.title for u in units] + ["최근 로그인"])
    sub_counts = dict(db.session.query(Submission.user_id, func.count()).group_by(Submission.user_id).all())
    for s in students_q().order_by(User.student_no).all():
        row = [s.student_no, s.name, counts.get(s.id, 0), sub_counts.get(s.id, 0)]
        for unit in units:
            ps = [p.id for p in unit.problems if p.published]
            row.append(f"{len(ac.get(s.id, set()) & set(ps))}/{len(ps)}")
        row.append(s.last_login.strftime("%Y-%m-%d %H:%M") if s.last_login else "")
        w.writerow(row)
    data = "﻿" + buf.getvalue()
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=student_progress.csv"})


# ------------------------------------------------------------------ 문제 관리
@bp.route("/problems", methods=["GET", "POST"])
def problems():
    if request.method == "POST":
        action = request.form.get("action")
        ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
        ps = Problem.query.filter(Problem.id.in_(ids)).all() if ids else []
        for p in ps:
            if action == "publish":
                p.published = True
            elif action == "unpublish":
                p.published = False
            elif action == "delete":
                db.session.delete(p)
        db.session.commit()
        flash(f"{len(ps)}개 문제에 적용했습니다.", "success")
        return redirect(url_for("admin.problems", unit=request.args.get("unit", "")))
    unit_id = request.args.get("unit", type=int)
    q = Problem.query
    if unit_id:
        q = q.filter_by(unit_id=unit_id)
    ps = q.order_by(Problem.number).all()
    tried = dict(db.session.query(Submission.problem_id, func.count(func.distinct(Submission.user_id)))
                 .group_by(Submission.problem_id).all())
    solved = dict(db.session.query(Submission.problem_id, func.count(func.distinct(Submission.user_id)))
                  .filter(Submission.status == "AC").group_by(Submission.problem_id).all())
    return render_template("admin/problems.html", ps=ps, units=Unit.query.order_by(Unit.order).all(),
                           unit_id=unit_id, tried=tried, solved=solved)


def _problem_form_save(p):
    f = request.form
    try:
        number = int(f.get("number", "0"))
    except ValueError:
        number = 0
    other = Problem.query.filter(Problem.number == number, Problem.id != (p.id or 0)).first()
    if number <= 0 or other:
        return "문제 번호가 비어 있거나 다른 문제와 겹칩니다."
    if not f.get("title", "").strip():
        return "제목을 입력하세요."
    try:
        tests = json.loads(f.get("tests_json", "[]"))
        quizzes = json.loads(f.get("quizzes_json", "[]"))
    except ValueError:
        return "테스트 케이스/퀴즈 데이터 형식 오류"
    if not tests:
        return "테스트 케이스를 1개 이상 입력하세요."
    for q in quizzes:
        if not q.get("question", "").strip() or str(q.get("answer", "")).strip() == "":
            return "퀴즈의 질문과 정답을 모두 입력하세요."
        if q.get("kind") == "choice" and len([o for o in q.get("options", []) if o.strip()]) < 2:
            return "객관식 퀴즈는 보기를 2개 이상 입력하세요."
    p.number = number
    p.unit_id = f.get("unit_id", type=int)
    data = {
        "title": f.get("title", "").strip(), "level": f.get("level", 1, type=int),
        "concept": f.get("concept", ""), "description": f.get("description", ""),
        "input_desc": f.get("input_desc", ""), "output_desc": f.get("output_desc", ""),
        "hint": f.get("hint", ""), "solution": f.get("solution", ""),
        "time_limit": max(0.5, min(f.get("time_limit", 2.0, type=float), 10.0)),
        "memory_limit": max(32, min(f.get("memory_limit", 256, type=int), 512)),
        "published": bool(f.get("published")), "tests": tests, "quizzes": quizzes,
    }
    apply_problem_data(p, data)
    return None


@bp.route("/problems/new", methods=["GET", "POST"])
@bp.route("/problems/<int:pid>/edit", methods=["GET", "POST"])
def problem_edit(pid=None):
    p = db.get_or_404(Problem, pid) if pid else Problem()
    if request.method == "POST":
        if pid is None:
            db.session.add(p)
        err = _problem_form_save(p)
        if err:
            db.session.rollback()
            flash(err, "error")
        else:
            db.session.commit()
            flash("문제를 저장했습니다.", "success")
            return redirect(url_for("admin.problem_edit", pid=p.id))
    if pid is None and request.method == "GET":
        p.number = (db.session.query(func.max(Problem.number)).scalar() or 1000) + 1
        p.level, p.time_limit, p.memory_limit, p.published = 1, 2.0, 256, True
    data = problem_to_dict(p) if p.id else {"tests": [], "quizzes": []}
    return render_template("admin/problem_edit.html", p=p, units=Unit.query.order_by(Unit.order).all(),
                           tests=data.get("tests", []), quizzes=data.get("quizzes", []))


@bp.post("/problems/<int:pid>/verify")
def problem_verify(pid):
    """모범 답안으로 테스트 케이스를 검증한다."""
    from .judge_client import _call
    from flask import current_app
    p = db.get_or_404(Problem, pid)
    if not p.solution.strip():
        return jsonify({"error": "모범 답안이 없습니다."}), 400
    tests = [{"input": t.input, "output": t.output} for t in p.testcases]
    res = _call(current_app._get_current_object(), "/judge",
                {"code": p.solution, "tests": tests, "time_limit": p.time_limit, "memory_limit": p.memory_limit},
                timeout=len(tests) * (p.time_limit + 2) + 30)
    return jsonify(res)


@bp.post("/problems/<int:pid>/rejudge")
def problem_rejudge(pid):
    p = db.get_or_404(Problem, pid)
    subs = Submission.query.filter_by(problem_id=p.id).all()
    for s in subs:
        s.status = "PENDING"
    db.session.commit()
    for s in subs:
        enqueue(s.id)
    flash(f"{len(subs)}개 제출을 재채점합니다.", "success")
    return redirect(request.referrer or url_for("admin.problems"))


@bp.route("/problems/export")
def problems_export():
    data = json.dumps(export_all(), ensure_ascii=False, indent=1)
    return Response(data, mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=problems_export.json"})


@bp.route("/problems/import", methods=["GET", "POST"])
def problems_import():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            flash("파일을 선택하세요.", "error")
        else:
            try:
                data = json.loads(f.read().decode("utf-8-sig"))
                n = load_problem_data(data, overwrite=bool(request.form.get("overwrite")))
                flash(f"{n}개 문제를 불러왔습니다.", "success")
                return redirect(url_for("admin.problems"))
            except (ValueError, KeyError) as e:
                db.session.rollback()
                flash(f"파일 형식 오류: {e}", "error")
    return render_template("admin/problems_import.html")


# ------------------------------------------------------------------ 단원 관리
@bp.route("/units", methods=["GET", "POST"])
def units():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save":
            uid = request.form.get("id", type=int)
            u = db.session.get(Unit, uid) if uid else Unit()
            if not uid:
                db.session.add(u)
            u.title = request.form.get("title", "").strip() or "새 단원"
            u.order = request.form.get("order", 0, type=int)
            u.summary = request.form.get("summary", "").strip()
            u.concept = request.form.get("concept", "")
            db.session.commit()
            flash("단원을 저장했습니다.", "success")
        elif action == "delete":
            u = db.get_or_404(Unit, request.form.get("id", type=int))
            if u.problems:
                flash("문제가 있는 단원은 삭제할 수 없습니다. 문제를 먼저 옮기거나 삭제하세요.", "error")
            else:
                db.session.delete(u)
                db.session.commit()
                flash("단원을 삭제했습니다.", "success")
        return redirect(url_for("admin.units"))
    return render_template("admin/units.html", units=Unit.query.order_by(Unit.order).all())


# ------------------------------------------------------------------ 제출 관리
@bp.route("/submissions")
def submissions():
    q = Submission.query.join(User).join(Problem)
    sno = request.args.get("student", "").strip()
    pnum = request.args.get("problem", type=int)
    status = request.args.get("status", "")
    if sno:
        q = q.filter((User.student_no.contains(sno)) | (User.name.contains(sno)))
    if pnum:
        q = q.filter(Problem.number == pnum)
    if status:
        q = q.filter(Submission.status == status)
    pg = q.order_by(Submission.id.desc()).paginate(page=request.args.get("page", 1, type=int),
                                                    per_page=40, error_out=False)
    return render_template("admin/submissions.html", pg=pg, student=sno, problem=pnum or "", status=status)


@bp.post("/submissions/<int:sid>/rejudge")
def submission_rejudge(sid):
    s = db.get_or_404(Submission, sid)
    s.status = "PENDING"
    db.session.commit()
    enqueue(s.id)
    flash("재채점을 시작했습니다.", "success")
    return redirect(url_for("student.submission_detail", sid=s.id))


# ------------------------------------------------------------------ 통계
@bp.route("/stats")
def stats():
    ps = Problem.query.order_by(Problem.number).all()
    sub_n = dict(db.session.query(Submission.problem_id, func.count()).group_by(Submission.problem_id).all())
    ac_n = dict(db.session.query(Submission.problem_id, func.count())
                .filter(Submission.status == "AC").group_by(Submission.problem_id).all())
    tried = dict(db.session.query(Submission.problem_id, func.count(func.distinct(Submission.user_id)))
                 .group_by(Submission.problem_id).all())
    solved = dict(db.session.query(Submission.problem_id, func.count(func.distinct(Submission.user_id)))
                  .filter(Submission.status == "AC").group_by(Submission.problem_id).all())
    quiz_rows = (db.session.query(Quiz.problem_id, func.count(QuizAttempt.id),
                                  func.sum(db.case((QuizAttempt.correct.is_(True), 1), else_=0)))
                 .join(QuizAttempt, QuizAttempt.quiz_id == Quiz.id).group_by(Quiz.problem_id).all())
    quiz = {pid: (n, c or 0) for pid, n, c in quiz_rows}
    rows = []
    for p in ps:
        n, c = quiz.get(p.id, (0, 0))
        rows.append({"p": p, "subs": sub_n.get(p.id, 0), "ac": ac_n.get(p.id, 0), "tried": tried.get(p.id, 0),
                     "solved": solved.get(p.id, 0), "quiz_n": n,
                     "quiz_rate": round(c * 100 / n) if n else None})
    # 학생 × 단원 진도표
    units = [u for u in Unit.query.order_by(Unit.order).all() if any(p.published for p in u.problems)]
    ac = {}
    for uid, pid in (db.session.query(Submission.user_id, Submission.problem_id)
                     .filter(Submission.status == "AC").distinct()):
        ac.setdefault(uid, set()).add(pid)
    matrix = []
    for s in students_q().order_by(User.student_no).all():
        cells = []
        for u in units:
            ids = {p.id for p in u.problems if p.published}
            done = len(ac.get(s.id, set()) & ids)
            cells.append({"done": done, "total": len(ids), "pct": round(done * 100 / len(ids)) if ids else 0})
        matrix.append({"s": s, "cells": cells, "total": len(ac.get(s.id, set()))})
    return render_template("admin/stats.html", rows=rows, units=units, matrix=matrix)


# ------------------------------------------------------------------ AI 설정
@bp.route("/ai", methods=["GET", "POST"])
def ai_settings():
    if request.method == "POST":
        f = request.form
        if f.get("action") == "clear_key":
            set_setting(f"ai_key_{f.get('provider')}", "")
            db.session.commit()
            flash("API 키를 삭제했습니다.", "success")
            return redirect(url_for("admin.ai_settings"))
        provider = f.get("ai_provider")
        if provider in ai.PROVIDERS:
            set_setting("ai_provider", provider)
        set_setting("ai_enabled", "1" if f.get("ai_enabled") else "0")
        set_setting("ai_allow_code", "1" if f.get("ai_allow_code") else "0")
        set_setting("ai_daily_limit", str(max(0, f.get("ai_daily_limit", 30, type=int))))
        set_setting("ai_max_tokens", str(max(200, min(f.get("ai_max_tokens", 1200, type=int), 4000))))
        set_setting("ai_extra_prompt", f.get("ai_extra_prompt", "")[:2000])
        for p in ai.PROVIDERS:
            model = f.get(f"ai_model_{p}", "").strip()
            if model:
                set_setting(f"ai_model_{p}", model)
            key = f.get(f"ai_key_{p}", "").strip()
            if key:
                set_setting(f"ai_key_{p}", encrypt(key))
        db.session.commit()
        flash("AI 설정을 저장했습니다.", "success")
        return redirect(url_for("admin.ai_settings"))
    keys = {p: mask_key(decrypt(get_setting(f"ai_key_{p}"))) for p in ai.PROVIDERS}
    models = {p: get_setting(f"ai_model_{p}") for p in ai.PROVIDERS}
    logs = AILog.query.order_by(AILog.id.desc()).limit(50).all()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    usage = AILog.query.filter(AILog.created_at >= today).count()
    return render_template("admin/ai.html", providers=ai.PROVIDERS, keys=keys, models=models, logs=logs,
                           usage=usage, s={k: get_setting(k) for k in
                                           ["ai_enabled", "ai_provider", "ai_allow_code", "ai_daily_limit",
                                            "ai_extra_prompt", "ai_max_tokens"]})


@bp.post("/ai/test")
def ai_test():
    cfg = ai.current_config()
    try:
        ans = ai.ask("당신은 테스트 응답기입니다. 한 문장으로만 답하세요.",
                     [{"role": "user", "content": "파이썬의 print 함수가 하는 일을 한 문장으로 설명하세요."}], cfg)
        return jsonify({"ok": True, "provider": ai.PROVIDERS[cfg["provider"]], "model": cfg["model"], "answer": ans})
    except ai.AIError as e:
        return jsonify({"ok": False, "error": str(e)})


@bp.route("/ai/logs")
def ai_logs():
    pg = AILog.query.order_by(AILog.id.desc()).paginate(page=request.args.get("page", 1, type=int),
                                                        per_page=30, error_out=False)
    return render_template("admin/ai_logs.html", pg=pg)


# ------------------------------------------------------------------ 사이트 설정
@bp.route("/settings", methods=["GET", "POST"])
def settings():
    keys = ["site_title", "announcement", "require_quiz", "show_hidden_cases", "default_password", "allow_custom_run"]
    if request.method == "POST":
        f = request.form
        set_setting("site_title", f.get("site_title", "").strip() or "파이썬 코딩 연습장")
        set_setting("announcement", f.get("announcement", "")[:3000])
        set_setting("default_password", f.get("default_password", "").strip())
        for k in ["require_quiz", "show_hidden_cases", "allow_custom_run"]:
            set_setting(k, "1" if f.get(k) else "0")
        db.session.commit()
        flash("설정을 저장했습니다.", "success")
        return redirect(url_for("admin.settings"))
    from flask import current_app
    judge_mode = "격리 채점 서버" if current_app.config.get("JUDGE_URL") else "로컬 실행(개발용)"
    admins = User.query.filter_by(is_admin=True).all()
    return render_template("admin/settings.html", s={k: get_setting(k) for k in keys},
                           judge_mode=judge_mode, admins=admins)


@bp.post("/settings/admins")
def add_admin():
    aid = request.form.get("admin_id", "").strip()
    pw = request.form.get("password", "")
    if not _valid_no(aid) or len(pw) < 8:
        flash("아이디(영문·숫자)와 8자 이상 비밀번호를 입력하세요.", "error")
    elif User.query.filter_by(student_no=aid).first():
        flash("이미 사용 중인 아이디입니다.", "error")
    else:
        u = User(student_no=aid, name=request.form.get("name", "").strip() or "관리자", is_admin=True,
                 must_change_password=False)
        u.set_password(pw)
        db.session.add(u)
        db.session.commit()
        flash("관리자를 추가했습니다.", "success")
    return redirect(url_for("admin.settings"))


@bp.post("/settings/admins/<int:uid>/delete")
def delete_admin(uid):
    u = db.get_or_404(User, uid)
    if not u.is_admin or u.id == current_user.id:
        flash("본인 계정은 삭제할 수 없습니다.", "error")
    else:
        db.session.delete(u)
        db.session.commit()
        flash("관리자를 삭제했습니다.", "success")
    return redirect(url_for("admin.settings"))
