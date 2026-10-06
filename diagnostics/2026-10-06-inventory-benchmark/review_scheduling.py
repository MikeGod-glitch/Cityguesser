"""Replay real saved inventories; do not contact APIs or write instance files."""
import json
from pathlib import Path
import benchmark as b

out = Path(__file__).parent / "candidate-scheduling-review"
out.mkdir(exist_ok=True)
before = b.read(Path(__file__).parent / "verified/live-end-prepared.json")
observed, current, candidates = b.snapshot_live()
(out / "current-prepared.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
result = {"baseline_at": "2026-10-06T17:08:29+08:00", "current": observed,
          "baseline_richness": b.richness(before, b.time.time()), "replays": {}}
print("LIVE", json.dumps(observed, ensure_ascii=False), flush=True)
for count in (300, 1000):
    result["replays"][str(count)] = {}
    for label, entries in (("before", before), ("now", current)):
        replay = b.selection_replay(entries, runs=30, questions=count)
        result["replays"][str(count)][label] = replay
        print(count, label, json.dumps({k: v for k, v in replay.items() if k != "rows"}), flush=True)
result["limitations"] = [
    "Actual inventories, offline selector replay; no browser image loading measurement.",
    "Baseline predates newest scheduling change; growth includes elapsed background work before that change.",
    "Same image ID repetition; visually similar scenes are not measured.",
]
(out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
start = b.time.monotonic()
while b.time.monotonic() - start < 60:
    b.time.sleep(min(20, 60 - (b.time.monotonic() - start)))
    latest, _, _ = b.snapshot_live()
    result.setdefault("live_followup", []).append(latest)
    print("FOLLOWUP", latest["observed_at"], latest["richness"], flush=True)
(out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
