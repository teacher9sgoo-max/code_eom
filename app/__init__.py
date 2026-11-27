import logging
import os
import secrets

import click
from flask import Flask, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.engine import Engine

from .extensions import csrf, db, login_manager


@event.listens_for(Engine, "connect")
def _sqlite_pragma(dbapi_conn, _):
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


def _secret_key(instance_path):
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(instance_path, "secret_key")
    if os.path.exists(path):
        return open(path).read().strip()
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    return key


def create_app(test_config=None):
    app = Flask(__name__, instance_path=os.environ.get("INSTANCE_PATH") or None)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.update(
        SECRET_KEY=_secret_key(app.instance_path),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "judge.db")),
        SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"timeout": 15}} if not os.environ.get("DATABASE_URL") else {},
        JUDGE_URL=os.environ.get("JUDGE_URL", ""),
        JUDGE_TOKEN=os.environ.get("JUDGE_TOKEN", ""),
        JUDGE_CONCURRENCY=int(os.environ.get("JUDGE_CONCURRENCY", "4")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 12,
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        WTF_CSRF_TIME_LIMIT=None,
    )
    if test_config:
        app.config.update(test_config)
    logging.basicConfig(level=logging.INFO)

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "로그인이 필요합니다."

    from .models import User

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .student import bp as student_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    from judge.runner import STATUS_LABELS
    from .utils import get_setting, render_md

    @app.context_processor
    def inject():
        return {"site_title": get_setting("site_title")}

    app.jinja_env.globals["STATUS_LABELS"] = STATUS_LABELS

    app.jinja_env.filters["md"] = render_md
    app.jinja_env.filters["dt"] = lambda d: d.strftime("%Y-%m-%d %H:%M") if d else "-"

    @app.before_request
    def force_password_change():
        if (current_user.is_authenticated and current_user.must_change_password
                and request.endpoint not in ("auth.change_password", "auth.logout", "static")):
            return redirect(url_for("auth.change_password"))

    @app.errorhandler(403)
    def e403(_):
        return render_template("error.html", code=403, message="접근 권한이 없습니다."), 403

    @app.errorhandler(404)
    def e404(_):
        return render_template("error.html", code=404, message="페이지를 찾을 수 없습니다."), 404

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    with app.app_context():
        db.create_all()
        _bootstrap(app)

    _register_cli(app)
    return app


def _bootstrap(app):
    """최초 실행 시 관리자 계정과 기본 문제를 만든다."""
    from .models import Problem, User
    from .seed import load_problem_file

    if not User.query.filter_by(is_admin=True).first():
        admin_id = os.environ.get("ADMIN_ID", "admin")
        admin_pw = os.environ.get("ADMIN_PASSWORD") or "admin1234"
        u = User(student_no=admin_id, name="관리자", is_admin=True,
                 must_change_password=not os.environ.get("ADMIN_PASSWORD"))
        u.set_password(admin_pw)
        db.session.add(u)
        db.session.commit()
        app.logger.warning("관리자 계정 생성: 아이디=%s", admin_id)
    if Problem.query.count() == 0 and os.environ.get("PYJUDGE_SKIP_SEED") != "1":
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "problems", "python_basic.json")
        if os.path.exists(path):
            n = load_problem_file(path)
            app.logger.info("기본 문제 %d개를 불러왔습니다.", n)


def _register_cli(app):
    from .models import User
    from .seed import load_problem_file

    @app.cli.command("create-admin")
    @click.argument("admin_id")
    @click.argument("password")
    def create_admin(admin_id, password):
        """관리자 계정을 만들거나 비밀번호를 재설정한다."""
        u = User.query.filter_by(student_no=admin_id).first()
        if u is None:
            u = User(student_no=admin_id, name="관리자", is_admin=True)
            db.session.add(u)
        u.is_admin = True
        u.must_change_password = False
        u.set_password(password)
        db.session.commit()
        click.echo(f"관리자 '{admin_id}' 저장 완료")

    @app.cli.command("backup-db")
    @click.argument("path", default="")
    def backup_db(path):
        """운영 중에도 안전하게 DB를 백업한다 (기본: 인스턴스 폴더/backup.db)."""
        import sqlite3
        src_path = db.engine.url.database
        path = path or os.path.join(app.instance_path, "backup.db")
        src, dst = sqlite3.connect(src_path), sqlite3.connect(path)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
        click.echo(f"백업 완료: {path}")

    @app.cli.command("restore-db")
    @click.argument("path")
    def restore_db(path):
        """백업 파일의 내용으로 DB를 덮어쓴다. 실행 후 웹 서버를 재시작한다."""
        import sqlite3
        src, dst = sqlite3.connect(path), sqlite3.connect(db.engine.url.database)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
        click.echo("복원 완료. 'docker compose restart web' 으로 재시작하세요.")

    @app.cli.command("import-problems")
    @click.argument("path")
    @click.option("--overwrite", is_flag=True, help="같은 번호의 문제를 덮어쓴다")
    def import_problems(path, overwrite):
        """JSON 문제 파일을 불러온다."""
        n = load_problem_file(path, overwrite=overwrite)
        click.echo(f"{n}개 문제를 불러왔습니다.")
