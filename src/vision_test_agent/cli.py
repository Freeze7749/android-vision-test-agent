"""Command line: vtest run "<test case>" --package <app>"""
import argparse
import re
import sys
from pathlib import Path

from .agent import run_test
from .device import Device, DeviceError
from .model import OllamaModel
from .report import save


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="vtest", description="Test an Android app from a plain-language test case")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run one test case on a connected device")
    run.add_argument("goal", help='for example: "Tap New note; Enter the title Milk; Tap Save. Expected: Milk is in the list."')
    run.add_argument("--package", default="com.example.buggynotes")
    run.add_argument("--bug", default="", help="switch on a seeded bug in the demo app")
    run.add_argument("--serial")
    run.add_argument("--no-screenshot", action="store_true", help="give the model only the element list")
    run.add_argument("--out", help="output folder (default: out/<test name>)")
    args = parser.parse_args(argv)

    name = re.sub(r"[^a-z0-9]+", "-", args.goal.lower())[:40].strip("-") or "test"
    out_dir = Path(args.out or Path("out") / name)
    device = Device(args.serial)
    try:
        result = run_test(
            device, OllamaModel(), name, args.goal, args.package, out_dir, args.bug, not args.no_screenshot
        )
        save(result, out_dir, device.full_log() if result.verdict == "fail" else b"")
    except (DeviceError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(f"{result.verdict.upper()}: {result.explanation}")
    for item in result.evidence:
        print(f"  evidence: {item}")
    print(f"Report: {out_dir / 'report.md'}")
    return 0 if result.verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
