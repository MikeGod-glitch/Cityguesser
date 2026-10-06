"""Collect photo feedback independently of question selection and photo stock."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

FEEDBACK_REASONS = {
    "indoor": "Indoor scene",
    "person_closeup": "Person close-up",
    "object_closeup": "Object close-up",
    "other": "Other",
}


def record_feedback(path, question, reporter, reason="unspecified"):
    """Count each anonymous reporter once per photo, without changing gameplay."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if reason not in FEEDBACK_REASONS and reason != "unspecified":
        raise ValueError("Unknown photo feedback reason")
    with closing(sqlite3.connect(path, timeout=2)) as database, database:
        database.execute("BEGIN IMMEDIATE")
        database.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                image_id TEXT PRIMARY KEY,
                city TEXT NOT NULL,
                image_url TEXT NOT NULL,
                source_url TEXT,
                first_reported_at REAL NOT NULL,
                last_reported_at REAL NOT NULL
            )
        """)
        database.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                image_id TEXT NOT NULL,
                reporter TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT 'unspecified',
                PRIMARY KEY (image_id, reporter)
            )
        """)
        columns = {row[1] for row in database.execute("PRAGMA table_info(reports)")}
        if "reason" not in columns:
            database.execute("ALTER TABLE reports ADD COLUMN reason TEXT NOT NULL DEFAULT 'unspecified'")
        database.execute(
            "INSERT OR IGNORE INTO photos VALUES (?, ?, ?, ?, ?, ?)",
            (question["image_id"], question["answer"], question["image_url"],
             question.get("source_url"), now, now),
        )
        inserted = database.execute(
            "INSERT OR IGNORE INTO reports (image_id, reporter, reason) VALUES (?, ?, ?)",
            (question["image_id"], reporter, reason),
        ).rowcount
        if reason != "unspecified" and not inserted:
            inserted = database.execute(
                "UPDATE reports SET reason = ? WHERE image_id = ? AND reporter = ? AND reason = 'unspecified'",
                (reason, question["image_id"], reporter),
            ).rowcount
        if inserted:
            database.execute("UPDATE photos SET last_reported_at = ? WHERE image_id = ?",
                             (now, question["image_id"]))


def list_feedback(path):
    """Read the collected queue locally; there is no public review endpoint."""
    path = Path(path).resolve()
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
        database.row_factory = sqlite3.Row
        photos = [dict(row) for row in database.execute("""
            SELECT photos.*, COUNT(reports.reporter) AS report_count
            FROM photos JOIN reports USING (image_id)
            GROUP BY photos.image_id
            ORDER BY report_count DESC, last_reported_at DESC, image_id
        """)]
        columns = {row[1] for row in database.execute("PRAGMA table_info(reports)")}
        reason_column = "reason" if "reason" in columns else "'unspecified'"
        reasons = {}
        for row in database.execute(
                f"SELECT image_id, {reason_column} AS reason, COUNT(*) AS count FROM reports GROUP BY image_id, 2"):
            label = FEEDBACK_REASONS.get(row["reason"], "Unspecified (legacy)")
            reasons.setdefault(row["image_id"], {})[label] = row["count"]
        for photo in photos:
            photo["reasons"] = reasons.get(photo["image_id"], {})
        return photos


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="List collected photo feedback; no review or blocking.")
    parser.add_argument("--path", type=Path,
                        default=Path(__file__).resolve().parent / "instance" / "photo-feedback.sqlite3")
    args = parser.parse_args()
    print(json.dumps(list_feedback(args.path), ensure_ascii=False, indent=2))
