"""문제 JSON 가져오기/내보내기."""
import json

from .extensions import db
from .models import Problem, Quiz, TestCase, Unit

PROBLEM_FIELDS = ["title", "level", "concept", "description", "input_desc", "output_desc",
                  "hint", "solution", "time_limit", "memory_limit", "published"]


def apply_problem_data(p, data):
    for f in PROBLEM_FIELDS:
        if f in data:
            setattr(p, f, data[f])
    if "tests" in data:
        p.testcases = [TestCase(order=i, input=t.get("input", ""), output=t.get("output", ""),
                                is_sample=bool(t.get("sample")))
                       for i, t in enumerate(data["tests"])]
    if "quizzes" in data:
        qs = []
        for i, q in enumerate(data["quizzes"]):
            quiz = Quiz(order=i, kind=q.get("kind", "choice"), question=q["question"],
                        answer=str(q["answer"]), explanation=q.get("explanation", ""))
            quiz.options = q.get("options", [])
            qs.append(quiz)
        p.quizzes = qs


def load_problem_data(data, overwrite=False):
    count = 0
    for ud in data.get("units", []):
        unit = Unit.query.filter_by(title=ud["title"]).first()
        if unit is None:
            unit = Unit(title=ud["title"])
            db.session.add(unit)
        unit.order = ud.get("order", unit.order or 0)
        unit.summary = ud.get("summary", unit.summary or "")
        unit.concept = ud.get("concept", unit.concept or "")
        db.session.flush()
        for pd in ud.get("problems", []):
            p = Problem.query.filter_by(number=pd["number"]).first()
            if p is not None and not overwrite:
                continue
            if p is None:
                p = Problem(number=pd["number"])
                db.session.add(p)
            p.unit_id = unit.id
            apply_problem_data(p, pd)
            count += 1
    db.session.commit()
    return count


def load_problem_file(path, overwrite=False):
    with open(path, encoding="utf-8") as f:
        return load_problem_data(json.load(f), overwrite=overwrite)


def problem_to_dict(p):
    d = {"number": p.number}
    for f in PROBLEM_FIELDS:
        d[f] = getattr(p, f)
    d["tests"] = [{"input": t.input, "output": t.output, "sample": t.is_sample} for t in p.testcases]
    d["quizzes"] = [{"kind": q.kind, "question": q.question, "options": q.options,
                     "answer": q.answer, "explanation": q.explanation} for q in p.quizzes]
    return d


def export_all():
    units = []
    for u in Unit.query.order_by(Unit.order).all():
        units.append({"order": u.order, "title": u.title, "summary": u.summary, "concept": u.concept,
                      "problems": [problem_to_dict(p) for p in u.problems]})
    return {"format": "pyjudge-problems/1", "units": units}
