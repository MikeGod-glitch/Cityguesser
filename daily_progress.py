"""Compact signed daily progress, suitable for browser and session storage."""

from datetime import date
import secrets

from itsdangerous import BadSignature, URLSafeSerializer

FIELDS = ("daily_date", "answer_mode", "daily_run_id", "game_stats", "question_resolved",
          "question_result", "question_points", "question_revealed", "selected_choice",
          "hint_level", "completion")


def read(token, secret):
    try:
        state = URLSafeSerializer(secret, salt="daily-progress-v1").loads(token)
        date.fromisoformat(state["daily_date"])
        if state["answer_mode"] not in {"text", "choice"} or not 0 <= state["game_stats"]["answered"] <= 10:
            return None
        return state
    except (BadSignature, ValueError, TypeError, KeyError):
        return None


def position(state):
    return state["game_stats"]["answered"] * 2 + int(bool(state.get("question_id")) and not state.get("question_resolved", False))


def capture(session, secret):
    if session.get("game_mode") != "daily":
        return
    session.setdefault("daily_run_id", secrets.token_urlsafe(12))
    state = {key: session[key] for key in FIELDS if key in session}
    question = session.get("current_question", {})
    state["question_id"] = question.get("question_id")
    answered = state["game_stats"]["answered"]
    state["question_index"] = max(0, answered - int(state.get("question_resolved", False)))
    token = URLSafeSerializer(secret, salt="daily-progress-v1").dumps(state)
    runs = dict(session.get("daily_runs", {}))
    runs[state["daily_date"]] = token
    # Browser storage keeps older runs; keep the cookie small.
    runs = {day: runs[day] for day in sorted(runs)[-2:]}
    if runs != session.get("daily_runs"):
        session["daily_runs"] = runs


def describe(token, secret):
    state = read(token, secret)
    if not state:
        return None
    return {"date": state["daily_date"], "answer_mode": state["answer_mode"],
            "run_id": state["daily_run_id"], "answered": state["game_stats"]["answered"],
            "position": position(state), "complete": state["game_stats"]["answered"] >= 10,
            "token": token}
