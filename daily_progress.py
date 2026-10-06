"""Compact signed daily progress, suitable for browser and session storage."""

from datetime import date
import secrets

from itsdangerous import BadSignature, URLSafeSerializer

FIELDS = ("daily_date", "answer_mode", "daily_run_id", "game_stats", "question_resolved",
          "question_result", "question_points", "question_revealed", "selected_choice",
          "hint_level", "completion", "current_question", "remaining")


def read(token, secret):
    try:
        # Dictionaries only come from Flask's already signed session cookie;
        # browser form values remain signed strings.
        state = token if isinstance(token, dict) else URLSafeSerializer(secret, salt="daily-progress-v1").loads(token)
        date.fromisoformat(state["daily_date"])
        if state["answer_mode"] not in {"text", "choice"} or not 0 <= state["game_stats"]["answered"] <= 10:
            return None
        return state
    except (BadSignature, ValueError, TypeError, KeyError):
        return None


def position(state):
    return state["game_stats"]["answered"] * 2 + int(bool(state.get("question_id")) and not state.get("question_resolved", False))


def latest_snapshot(day, tokens, secret):
    """Prefer newer progress within a run; preserve the existing different-run policy."""
    saved = None
    for token in tokens:
        candidate = read(token, secret) if token else None
        if not candidate or candidate["daily_date"] != day:
            continue
        if (saved is None or candidate["daily_run_id"] != saved["daily_run_id"]
                or position(candidate) > position(saved)):
            saved = candidate
    return saved


def capture(session, secret):
    if session.get("game_mode") != "daily":
        return
    session.setdefault("daily_run_id", secrets.token_urlsafe(12))
    state = {key: session[key] for key in FIELDS if key in session}
    question = session.get("current_question", {})
    state["question_id"] = question.get("question_id")
    # Ten recent entries cover the whole daily round without growing each token
    # with the player's entire ordinary-game history.
    for key in ("recent_images", "recent_cities"):
        state[key] = list(session.get(key, []))[-10:]
    runs = dict(session.get("daily_runs", {}))
    # Store structured data inside the signed cookie so compression can share
    # repeated photo/history fields. Sign browser snapshots only when rendering.
    runs[state["daily_date"]] = state
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
            "token": URLSafeSerializer(secret, salt="daily-progress-v1").dumps(state)}
