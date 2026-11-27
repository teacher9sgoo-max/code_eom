import time
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .models import User

bp = Blueprint("auth", __name__)

_failures = defaultdict(list)  # ip -> [timestamps]
MAX_FAIL, WINDOW = 10, 300


def _blocked(ip):
    now = time.time()
    _failures[ip] = [t for t in _failures[ip] if now - t < WINDOW]
    return len(_failures[ip]) >= MAX_FAIL


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard" if current_user.is_admin else "student.home"))
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        if _blocked(ip):
            flash("로그인 시도가 너무 많습니다. 5분 후 다시 시도하세요.", "error")
            return render_template("auth/login.html"), 429
        sid = request.form.get("student_no", "").strip()
        pw = request.form.get("password", "")
        user = User.query.filter_by(student_no=sid).first()
        if user and user.is_active and user.check_password(pw):
            _failures.pop(ip, None)
            login_user(user, remember=bool(request.form.get("remember")))
            user.last_login = datetime.now()
            db.session.commit()
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("admin.dashboard" if user.is_admin else "student.home"))
        _failures[ip].append(time.time())
        flash("학번 또는 비밀번호가 올바르지 않습니다.", "error")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        cur = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not current_user.check_password(cur):
            flash("현재 비밀번호가 올바르지 않습니다.", "error")
        elif len(new) < 4:
            flash("새 비밀번호는 4자 이상이어야 합니다.", "error")
        elif new != confirm:
            flash("새 비밀번호가 서로 다릅니다.", "error")
        elif new == current_user.student_no:
            flash("학번과 같은 비밀번호는 사용할 수 없습니다.", "error")
        else:
            current_user.set_password(new)
            current_user.must_change_password = False
            db.session.commit()
            flash("비밀번호가 변경되었습니다.", "success")
            return redirect(url_for("admin.dashboard" if current_user.is_admin else "student.home"))
    return render_template("auth/password.html")
