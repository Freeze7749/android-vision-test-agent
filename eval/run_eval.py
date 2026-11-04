"""Run test cases against the demo app with seeded bugs on and off, and score the verdicts.

    python eval/run_eval.py --split dev
    python eval/run_eval.py --split test --no-screenshot

The dev bugs are for tuning the prompt. The test bugs stay unseen until the final, single run.
"""
import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from vision_test_agent.agent import run_test
from vision_test_agent.device import Device
from vision_test_agent.model import OllamaModel
from vision_test_agent.report import save

HERE = Path(__file__).parent
PACKAGE = "com.example.buggynotes"

# Format: steps separated by semicolons, then "Expected:" and the expected result.
GOALS = {
    "create_note": "Tap New note; Enter the title Buy milk; Tap Save. Expected: Buy milk appears in the notes list.",
    "edit_note": "Open the note called Welcome; Change its title to Hello; Tap Save. Expected: Hello appears in the notes list and Welcome does not.",
    "delete_note": "Open the note called Welcome; Tap Delete. Expected: Welcome is no longer in the notes list.",
    "search": "Type Meeting in the search box; Tap Search. Expected: Meeting notes is listed while Welcome and Shopping list are not.",
    "count": "Open Settings; Turn on Show note count; Tap Back to notes. Expected: the heading shows the same number as the number of notes listed.",
    "version": "Open Settings. Expected: the page shows Version 1.0.",
    "titles_visible": "Expected: the note titles Welcome, Meeting notes and Shopping list can be read clearly in the screenshot.",
    "settings_return": "Open Settings; Tap Back to notes. Expected: the My notes heading and the notes list are shown.",
}

# Each case is (test, bug). A case with its bug in AFFECTS must fail; every other case must pass.
AFFECTS = {
    "save_lost": ["create_note"],
    "wrong_search": ["search"],
    "crash_settings": ["version", "settings_return"],
    "missing_delete": ["delete_note"],
    "edit_not_saved": ["edit_note"],
    "count_wrong": ["count"],
    "wrong_version": ["version"],
    "invisible_titles": ["titles_visible"],
    "crash_on_delete": ["delete_note"],
    "freeze_search": ["search"],
}
SPLITS = {
    "dev": {
        "bugs": ["save_lost", "wrong_search", "crash_settings", "missing_delete"],
        "clean": ["create_note", "search", "version", "settings_return", "delete_note"],
        "unaffected": [("version", "save_lost"), ("create_note", "wrong_search"), ("create_note", "crash_settings"), ("search", "missing_delete")],
    },
    "test": {
        "bugs": ["edit_not_saved", "count_wrong", "wrong_version", "invisible_titles", "crash_on_delete", "freeze_search"],
        "clean": ["edit_note", "count", "titles_visible", "version", "delete_note", "search"],
        "unaffected": [("version", "edit_not_saved"), ("create_note", "count_wrong"), ("search", "wrong_version"), ("version", "invisible_titles"), ("create_note", "crash_on_delete"), ("version", "freeze_search")],
    },
}


def cases(split: str) -> list[dict]:
    plan = SPLITS[split]
    rows = [{"test": test, "bug": bug, "expected": "fail"} for bug in plan["bugs"] for test in AFFECTS[bug]]
    rows += [{"test": test, "bug": "", "expected": "pass"} for test in plan["clean"]]
    rows += [{"test": test, "bug": bug, "expected": "pass"} for test, bug in plan["unaffected"]]
    return rows


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float]:
    """95% confidence interval for a proportion. Small sets deserve wide error bars."""
    if total == 0:
        return [0.0, 0.0]
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3)]


def score(rows: list[dict]) -> dict:
    buggy = [row for row in rows if row["expected"] == "fail"]
    healthy = [row for row in rows if row["expected"] == "pass"]
    detected = sum(row["verdict"] == "fail" for row in buggy)
    false_alarms = sum(row["verdict"] == "fail" for row in healthy)
    passed = sum(row["verdict"] == "pass" for row in healthy)
    return {
        "runs": len(rows),
        "correct": sum(row["verdict"] == row["expected"] for row in rows),
        "buggy_runs": len(buggy),
        "bugs_detected": detected,
        "detection_rate": round(detected / len(buggy), 3) if buggy else None,
        "detection_95ci": wilson_interval(detected, len(buggy)),
        "detected_by_crash_check": sum(row["verdict"] == "fail" and row["found_by"] == "program" for row in buggy),
        "healthy_runs": len(healthy),
        "healthy_passed": passed,
        "false_alarms": false_alarms,
        "false_alarm_rate": round(false_alarms / len(healthy), 3) if healthy else None,
        "false_alarm_95ci": wilson_interval(false_alarms, len(healthy)),
        "unsure": sum(row["verdict"] == "unsure" for row in rows),
        "avg_steps": round(sum(row["steps"] for row in rows) / len(rows), 1) if rows else 0,
        "avg_seconds": round(sum(row["seconds"] for row in rows) / len(rows), 1) if rows else 0,
        "rejected_evidence": sum(row["rejected_evidence"] for row in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    parser.add_argument("--no-screenshot", action="store_true")
    parser.add_argument("--only", help="run only cases whose test or bug has this name; nothing is saved")
    parser.add_argument("--adb")
    args = parser.parse_args()

    mode = "elements_only" if args.no_screenshot else "with_screenshot"
    name = f"{mode}_{args.split}"
    results = HERE / "results"
    results.mkdir(exist_ok=True)
    checkpoint = results / f"{name}.partial.jsonl"
    finished = {}
    if checkpoint.exists() and not args.only:
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            finished[(row["test"], row["bug"])] = row

    device, model, rows = Device(adb=args.adb), OllamaModel(), []
    for case in cases(args.split):
        if args.only and args.only not in (case["test"], case["bug"]):
            continue
        key = (case["test"], case["bug"])
        if key in finished:
            rows.append(finished[key])
            continue
        out_dir = Path("out") / name / f"{case['test']}__{case['bug'] or 'clean'}"
        result = run_test(
            device, model, case["test"], GOALS[case["test"]], PACKAGE, out_dir, case["bug"], not args.no_screenshot
        )
        save(result, out_dir, device.full_log() if result.verdict == "fail" else b"")
        row = {
            **case,
            "verdict": result.verdict,
            "explanation": result.explanation[:300],
            "evidence": result.evidence,
            "found_by": result.found_by,
            "steps": len(result.steps),
            "seconds": result.seconds,
            "rejected_evidence": result.rejected_evidence,
        }
        rows.append(row)
        print(f"{case['test']} bug={case['bug'] or '-'}: expected {case['expected']}, got {result.verdict} ({result.seconds}s)", flush=True)
        if not args.only:
            with checkpoint.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row) + "\n")

    summary = score(rows)
    summary.update(model=model.name, mode=mode, split=args.split, date=datetime.now(timezone.utc).date().isoformat())
    print(json.dumps(summary, indent=2))
    if not args.only:
        (results / f"{name}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=1), encoding="utf-8", newline="\n")
        checkpoint.unlink(missing_ok=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
