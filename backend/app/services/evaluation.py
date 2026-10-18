import re


def score_answers(answers: list[dict]) -> dict:
    texts = [item.get("answer_text", "") for item in answers]
    lengths = [len(text.split()) for text in texts]
    count = len(texts) or 1
    detail = sum(length >= 35 for length in lengths)
    outcomes = sum(bool(re.search(r"\b(result|outcome|increase|decrease|improv|saved|grew|reduced|delivered|launched|percent|%)\b", text, re.I)) for text in texts)
    ownership = sum(bool(re.search(r"\b(I|my)\b", text, re.I)) for text in texts)
    reflection = sum(bool(re.search(r"\b(learn|feedback|different|next time|realized|changed)\b", text, re.I)) for text in texts)
    scores = {
        "clarity": min(92, 48 + round(detail / count * 38)),
        "impact": min(94, 42 + round(outcomes / count * 46)),
        "ownership": min(95, 45 + round(ownership / count * 45)),
        "growth": min(93, 44 + round(reflection / count * 44)),
    }
    return {
        "evaluation_type": "heuristic",
        "rubric_version": "heuristic-v1",
        "notice": "Deterministic heuristic practice feedback; not AI evaluation or a hiring recommendation.",
        "scores": scores,
        "overall_score": round(sum(scores.values()) / len(scores)),
    }


