"""문제 원본 작성용 도우미."""


def U(title, summary, concept, problems):
    return {"title": title, "summary": summary, "concept": concept.strip(), "problems": problems}


def P(title, level, concept, description, input_desc, output_desc, solution, inputs,
      quiz=None, hint="", samples=1, time_limit=2.0):
    return {"title": title, "level": level, "concept": concept.strip(), "description": description.strip(),
            "input_desc": input_desc.strip(), "output_desc": output_desc.strip(), "solution": solution.strip() + "\n",
            "inputs": inputs, "quizzes": quiz or [], "hint": hint.strip(), "samples": samples,
            "time_limit": time_limit}


def C(question, options, answer, explanation=""):
    return {"kind": "choice", "question": question, "options": options, "answer": str(answer),
            "explanation": explanation}


def S(question, answer, explanation=""):
    return {"kind": "short", "question": question, "options": [], "answer": answer, "explanation": explanation}
