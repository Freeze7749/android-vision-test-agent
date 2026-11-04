"""Write the outcome of a test: a Markdown bug report with step screenshots, plus Jira fields."""
import json
from dataclasses import asdict
from pathlib import Path

from .agent import Result


def analyze_log(log_file: Path) -> str:
    """Use Android Bug Triage Agent on the log if it is installed. It is optional."""
    try:
        from triage_agent.pipeline import triage
    except ImportError:
        return ""
    result = triage(log_file, analyzer="rules")
    lines = [f"Log triage: `{result.predicted_category}`. {result.summary}"]
    for cause in result.causes[:2]:
        lines += [f"- {log_file.name}:{ev.line}: `{ev.quote[:160]}`" for ev in cause.evidence[:3]]
    return "\n".join(lines)


def to_markdown(result: Result, triage_text: str = "") -> str:
    rows = [
        f"# {result.test_id}: {result.verdict.upper()}",
        "",
        f"**Test case:** {result.goal}",
        "",
        f"**Explanation:** {result.explanation}",
        "",
        f"Decided by: {result.found_by}. Steps: {len(result.steps)}. Time: {result.seconds} s.",
        "",
        "## Evidence",
        *[f"- `{item}`" for item in result.evidence],
        "",
        "## Steps",
    ]
    for step in result.steps:
        target = f" `{step.target}`" if step.target else ""
        rows += [
            f"{step.number}. {step.instruction}: **{step.action}**{target}. {step.reason}",
            f"   ![step {step.number}]({step.screenshot})",
        ]
    if triage_text:
        rows += ["", "## Log analysis", triage_text]
    return "\n".join(rows) + "\n"


def to_jira_fields(result: Result, project_key: str = "PROJ") -> dict:
    """The `fields` object of Jira's create-issue request. Nothing is sent anywhere."""
    steps = "\n".join(f"# {step.instruction}" for step in result.steps)
    evidence = "\n".join(f"* {{{{{item}}}}}" for item in result.evidence)
    return {
        "project": {"key": project_key},
        "issuetype": {"name": "Bug"},
        "summary": f"{result.test_id} failed: {result.goal[:80]}",
        "labels": ["auto-test", f"found-by-{result.found_by}"],
        "description": f"h3. Explanation\n{result.explanation}\nh3. Steps\n{steps}\nh3. Evidence\n{evidence}",
    }


def save(result: Result, out_dir: Path, log: bytes = b"", project_key: str = "PROJ") -> None:
    """Keep the run's record. A failure also gets the log, its analysis and a draft bug report."""
    triage_text = ""
    if result.verdict == "fail" and log:
        log_file = out_dir / "device.log"
        log_file.write_bytes(log)
        triage_text = analyze_log(log_file)
    (out_dir / "report.md").write_text(to_markdown(result, triage_text), encoding="utf-8")
    (out_dir / "result.json").write_text(json.dumps(asdict(result), indent=1), encoding="utf-8")
    if result.verdict == "fail":
        (out_dir / "jira_fields.json").write_text(
            json.dumps(to_jira_fields(result, project_key), indent=2, ensure_ascii=False), encoding="utf-8"
        )
