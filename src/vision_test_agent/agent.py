"""The test loop. The program walks through the steps; the model only does one small job at a time."""
import time
from dataclasses import dataclass, field
from pathlib import Path

from .device import Device, DeviceError, Element
from .model import ModelError, StepAction

_MAX_RETRIES = 2  # Extra tries for one model question after an unusable answer.
_MAX_SCROLLS = 2  # Scrolls allowed while looking for the element of one step.
_SETTLE_SECONDS = 1.0
_CRASH_WORDS = ("FATAL EXCEPTION", "Fatal signal")
_DIALOG_WORDS = ("Application Error", "Application Not Responding", "isn't responding")


@dataclass
class Step:
    number: int
    instruction: str
    action: str
    target: str = ""
    reason: str = ""
    screenshot: str = ""


@dataclass
class Result:
    test_id: str
    goal: str
    verdict: str  # pass, fail or unsure
    explanation: str
    evidence: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    seconds: float = 0.0
    found_by: str = "model"  # "model", or "program" when a crash check decided
    rejected_evidence: int = 0


class _Stop(Exception):
    """Ends the run with a verdict."""

    def __init__(self, verdict: str, explanation: str, evidence=(), found_by: str = "model"):
        super().__init__(explanation)
        self.verdict, self.explanation, self.evidence, self.found_by = verdict, explanation, list(evidence), found_by


def parse_test_case(text: str) -> tuple[list[str], str]:
    """Split "Do this; Do that. Expected: result" into its steps and expected result."""
    before, separator, expected = text.partition("Expected:")
    if not separator or not expected.strip():
        raise ValueError('A test case needs an expected result, for example: "Tap Save. Expected: Saved is shown."')
    steps = [step.strip(" .") for step in before.split(";")]
    return [step for step in steps if step], expected.strip()


def _screen_text(elements: list[Element]) -> str:
    return "\n".join(element.describe() for element in elements) or "(nothing readable)"


def _check_step(action: StepAction, elements: list[Element]) -> str:
    """Return what is wrong with the model's choice, or "" if the program can carry it out."""
    if action.action not in ("tap", "type"):
        return ""
    element = next((item for item in elements if item.number == action.element), None)
    if element is None:
        return f"element {action.element} is not on the screen."
    # A small model sometimes leaves the label empty; a label that disagrees is the real warning sign.
    if action.element_label.strip() and action.element_label.strip().lower() != element.label.lower():
        return f"element {action.element} is labelled '{element.label}', not '{action.element_label}'. Check the list."
    if action.action == "tap" and not element.tappable:
        return f"element {action.element} cannot be tapped."
    if action.action == "type" and (not element.typable or not action.text):
        return f"element {action.element} cannot take text, or no text was given."
    return ""


def _crash_proof(device: Device) -> str:
    """A crash decides the test without asking the model. Return the proof, or ""."""
    for line in device.crash_log().splitlines():
        if any(word in line for word in _CRASH_WORDS):
            return line.strip()
    focus = device.foreground()
    return focus if any(word in focus for word in _DIALOG_WORDS) else ""


class _Run:
    def __init__(self, device: Device, model, out_dir: Path, use_screenshot: bool):
        self.device, self.model, self.out_dir, self.use_screenshot = device, model, out_dir, use_screenshot
        self.steps: list[Step] = []
        self.rejected = 0
        self.shots = 0

    def _look(self) -> tuple[bytes, list[Element], str]:
        """Check for a crash, then read the screen. Slow or frozen screens get a few tries."""
        last_error = None
        for _ in range(3):
            proof = _crash_proof(self.device)
            if proof:
                raise _Stop("fail", "The app crashed or stopped responding.", [proof], "program")
            try:
                png, elements = self.device.screenshot(), self.device.elements()
            except DeviceError as error:
                last_error = error
                time.sleep(5)
                continue
            self.shots += 1
            image = f"screen_{self.shots:02d}.png"
            (self.out_dir / image).write_bytes(png)
            return png, elements, image
        raise _Stop("unsure", f"The screen could not be read: {last_error}")

    def _choose(self, instruction: str, png: bytes, elements: list[Element]) -> StepAction:
        note = ""
        for _ in range(_MAX_RETRIES + 1):
            prompt = f"Step: {instruction}\n\nOn screen now:\n{_screen_text(elements)}"
            if note:
                prompt += f"\n\nProblem with your last answer: {note}"
            try:
                action = self.model.choose_step(prompt, png if self.use_screenshot else None)
                note = _check_step(action, elements)
            except ModelError as error:
                note = str(error)
            if not note:
                return action
        raise _Stop("unsure", f"The model kept giving unusable answers on step '{instruction}': {note}")

    def do_step(self, instruction: str) -> None:
        for _ in range(_MAX_SCROLLS + 1):
            png, elements, image = self._look()
            action = self._choose(instruction, png, elements)
            by_number = {element.number: element for element in elements}
            target = by_number[action.element].label if action.action in ("tap", "type") else ""
            number = len(self.steps) + 1
            self.steps.append(Step(number, instruction, action.action, target, action.reason, image))

            if action.action == "impossible":
                shown = [element.label for element in elements if element.label]
                raise _Stop("fail", f"Could not do step '{instruction}': {action.reason}", shown)
            if action.action == "tap":
                self.device.tap(by_number[action.element])
            elif action.action == "type":
                self.device.type_text(by_number[action.element], action.text)
            elif action.action == "back":
                self.device.back()
            else:
                self.device.scroll_down()
            time.sleep(_SETTLE_SECONDS)
            if action.action != "scroll":
                return
        raise _Stop("fail", f"Could not find what step '{instruction}' needs, even after scrolling.")

    def judge(self, expected: str) -> None:
        png, elements, image = self._look()
        by_number = {element.number: element for element in elements}
        note = ""
        for _ in range(_MAX_RETRIES + 1):
            prompt = f"Expected result: {expected}\n\nOn screen now:\n{_screen_text(elements)}"
            if note:
                prompt += f"\n\nProblem with your last answer: {note}"
            try:
                verdict = self.model.judge(prompt, png if self.use_screenshot else None)
            except ModelError as error:
                note = str(error)
                continue
            self.rejected += sum(number not in by_number for number in verdict.evidence)
            cited = [by_number[number].label for number in verdict.evidence if number in by_number]
            # A verdict with nothing real behind it must not count as a result.
            if verdict.verdict == "unsure" or cited:
                self.steps.append(Step(len(self.steps) + 1, "Check the expected result", "judge", "", verdict.reason, image))
                raise _Stop(verdict.verdict, verdict.reason, cited)
            note = f"a {verdict.verdict} verdict needs at least one number from 1 to {len(elements)} in evidence, such as [1]."
        raise _Stop("unsure", f"The model gave no usable verdict: {note}")


def run_test(
    device: Device,
    model,
    test_id: str,
    goal: str,
    package: str,
    out_dir: Path,
    bug: str = "",
    use_screenshot: bool = True,
) -> Result:
    instructions, expected = parse_test_case(goal)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    device.launch(package, bug)
    run = _Run(device, model, out_dir, use_screenshot)
    try:
        for instruction in instructions:
            run.do_step(instruction)
        run.judge(expected)
    except _Stop as stop:
        return Result(
            test_id, goal, stop.verdict, stop.explanation, stop.evidence, run.steps,
            round(time.time() - started, 1), stop.found_by, run.rejected,
        )
    raise AssertionError("judge always ends the run")
