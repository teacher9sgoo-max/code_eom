"""문제 원본(problems_a.py, problems_b.py)의 모범 답안을 실행해 정답 출력을 만들고
problems/python_basic.json 으로 저장한다.

    python tools/build_problems.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from judge.runner import run_once  # noqa: E402
import problems_a  # noqa: E402
import problems_b  # noqa: E402


def main():
    units_out = []
    number = 1001
    errors = 0
    for order, u in enumerate(problems_a.UNITS + problems_b.UNITS, 1):
        probs = []
        for p in u["problems"]:
            tests = []
            for i, inp in enumerate(p["inputs"]):
                inp = inp if inp.endswith("\n") or inp == "" else inp + "\n"
                r = run_once(p["solution"], inp, time_limit=10, memory_mb=512)
                if r["status"] != "OK":
                    print(f"[오류] {number} {p['title']} 입력#{i+1}: {r['status']} {r['error'][-300:]}")
                    errors += 1
                tests.append({"input": inp, "output": r["output"], "sample": i < p["samples"]})
            for q in p["quizzes"]:
                if q["kind"] == "choice":
                    assert 0 <= int(q["answer"]) < len(q["options"]), (number, q)
            probs.append({
                "number": number, "title": p["title"], "level": p["level"], "concept": p["concept"],
                "description": p["description"], "input_desc": p["input_desc"], "output_desc": p["output_desc"],
                "hint": p["hint"], "solution": p["solution"], "time_limit": p["time_limit"], "memory_limit": 256,
                "published": True, "tests": tests, "quizzes": p["quizzes"],
            })
            number += 1
        units_out.append({"order": order, "title": u["title"], "summary": u["summary"],
                          "concept": u["concept"], "problems": probs})
    out = os.path.join(ROOT, "problems", "python_basic.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"format": "pyjudge-problems/1", "units": units_out}, f, ensure_ascii=False, indent=1)
    total = sum(len(u["problems"]) for u in units_out)
    print(f"단원 {len(units_out)}개, 문제 {total}개 저장 → {out} (오류 {errors})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
