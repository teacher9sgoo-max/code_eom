import json
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def now():
    return datetime.now()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_no = db.Column(db.String(40), unique=True, nullable=False, index=True)  # 학번 (관리자는 아이디)
    name = db.Column(db.String(60), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_active_flag = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now)
    last_login = db.Column(db.DateTime)

    submissions = db.relationship("Submission", backref="user", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def is_active(self):
        return self.is_active_flag

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Unit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order = db.Column(db.Integer, default=0, index=True)
    title = db.Column(db.String(100), nullable=False)
    summary = db.Column(db.String(300), default="")
    concept = db.Column(db.Text, default="")  # 단원 전체 개념 정리
    problems = db.relationship("Problem", backref="unit", lazy="select", order_by="Problem.number")


class Problem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, unique=True, nullable=False, index=True)  # 문제 번호 (예: 1001)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"))
    title = db.Column(db.String(150), nullable=False)
    level = db.Column(db.Integer, default=1)  # 1~5
    concept = db.Column(db.Text, default="")  # 이 문제에 필요한 개념
    description = db.Column(db.Text, default="")
    input_desc = db.Column(db.Text, default="")
    output_desc = db.Column(db.Text, default="")
    hint = db.Column(db.Text, default="")
    solution = db.Column(db.Text, default="")  # 모범 답안 (관리자만 열람)
    time_limit = db.Column(db.Float, default=2.0)
    memory_limit = db.Column(db.Integer, default=256)
    published = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now)

    testcases = db.relationship("TestCase", backref="problem", order_by="TestCase.order",
                                cascade="all, delete-orphan")
    quizzes = db.relationship("Quiz", backref="problem", order_by="Quiz.order",
                              cascade="all, delete-orphan")
    submissions = db.relationship("Submission", backref="problem", lazy="dynamic",
                                  cascade="all, delete-orphan")

    @property
    def samples(self):
        return [t for t in self.testcases if t.is_sample]


class TestCase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problem.id"), nullable=False, index=True)
    order = db.Column(db.Integer, default=0)
    input = db.Column(db.Text, default="")
    output = db.Column(db.Text, default="")
    is_sample = db.Column(db.Boolean, default=False)


class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problem.id"), nullable=False, index=True)
    order = db.Column(db.Integer, default=0)
    kind = db.Column(db.String(10), default="choice")  # choice | short
    question = db.Column(db.Text, nullable=False)
    options_json = db.Column(db.Text, default="[]")
    answer = db.Column(db.Text, nullable=False)  # choice: 0부터 시작하는 번호, short: 정답 문자열(| 로 복수 정답)
    explanation = db.Column(db.Text, default="")

    @property
    def options(self):
        try:
            return json.loads(self.options_json or "[]")
        except ValueError:
            return []

    @options.setter
    def options(self, value):
        self.options_json = json.dumps(value, ensure_ascii=False)

    def check(self, given: str) -> bool:
        given = (given or "").strip()
        if self.kind == "choice":
            return given == str(self.answer).strip()
        norm = lambda s: " ".join(s.replace("\r", "").split()).lower()
        return any(norm(given) == norm(a) for a in str(self.answer).split("|"))


class QuizAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quiz.id"), nullable=False, index=True)
    answer = db.Column(db.Text, default="")
    correct = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now)
    quiz = db.relationship("Quiz", backref=db.backref("attempts", cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("quiz_attempts", cascade="all, delete-orphan"))


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problem.id"), nullable=False, index=True)
    code = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(10), default="PENDING", index=True)
    passed = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=0)
    max_time = db.Column(db.Float, default=0)
    result_json = db.Column(db.Text, default="{}")
    created_at = db.Column(db.DateTime, default=now, index=True)
    judged_at = db.Column(db.DateTime)

    @property
    def result(self):
        try:
            return json.loads(self.result_json or "{}")
        except ValueError:
            return {}


class AILog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    problem_id = db.Column(db.Integer, db.ForeignKey("problem.id"), index=True)
    kind = db.Column(db.String(20), default="chat")  # chat | quiz | submission
    question = db.Column(db.Text, default="")
    answer = db.Column(db.Text, default="")
    provider = db.Column(db.String(20), default="")
    model = db.Column(db.String(80), default="")
    ok = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now, index=True)
    user = db.relationship("User", backref=db.backref("ai_logs", cascade="all, delete-orphan"))
    problem = db.relationship("Problem", backref=db.backref("ai_logs", cascade="all, delete-orphan"))


class Setting(db.Model):
    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.Text, default="")
