"""The loop with a fake phone and a scripted model, so no device or Ollama is needed."""
import json

import pytest

from vision_test_agent.agent import parse_test_case, run_test
from vision_test_agent.device import DeviceError, Element, parse_elements
from vision_test_agent.model import ModelError, StepAction, Verdict
from vision_test_agent.report import save, to_jira_fields, to_markdown

BUTTON = Element(1, "Save", 100, 200, tappable=True, typable=False)
BOX = Element(2, "Title", 100, 300, tappable=True, typable=True)
TEXT = Element(3, "Buy milk", 100, 400, tappable=False, typable=False)
LABELS = {1: "Save", 2: "Title", 3: "Buy milk"}
GOAL = "Enter Buy milk; Tap Save. Expected: Buy milk is listed."
ONE_STEP = "Tap Save. Expected: Buy milk is listed."


class FakeDevice:
    def __init__(self, crash_after=None, unreadable=False, focus="com.example/.Main"):
        self.calls, self.crash_after, self.unreadable, self.focus = [], crash_after, unreadable, focus

    def launch(self, package, bug=""):
        self.calls.append(("launch", package, bug))

    def screenshot(self):
        return b"png"

    def elements(self):
        if self.unreadable:
            raise DeviceError("uiautomator timed out")
        return [BUTTON, BOX, TEXT]

    def foreground(self):
        return self.focus

    def crash_log(self):
        taps = sum(call[0] == "tap" for call in self.calls)
        if self.crash_after is not None and taps >= self.crash_after:
            return "11-04 10:00:00.000  1  2 E AndroidRuntime: FATAL EXCEPTION: main"
        return ""

    def tap(self, element):
        self.calls.append(("tap", element.label))

    def type_text(self, element, text):
        self.calls.append(("type", element.label, text))

    def back(self):
        self.calls.append(("back",))

    def scroll_down(self):
        self.calls.append(("scroll",))


class ScriptedModel:
    def __init__(self, steps=(), verdicts=()):
        self.steps, self.verdicts = list(steps), list(verdicts)
        self.step_prompts, self.verdict_prompts, self.images = [], [], []

    def _next(self, answers):
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def choose_step(self, prompt, screenshot):
        self.step_prompts.append(prompt)
        self.images.append(screenshot)
        return self._next(self.steps)

    def judge(self, prompt, screenshot):
        self.verdict_prompts.append(prompt)
        self.images.append(screenshot)
        return self._next(self.verdicts)


def _step(action, element=0, text="", label=None):
    return StepAction(
        reason="because", action=action, element=element, text=text,
        element_label=LABELS.get(element, "") if label is None else label,
    )


def _verdict(verdict, *evidence):
    return Verdict(reason="seen", verdict=verdict, evidence=list(evidence))


def _run(device, model, tmp_path, goal=ONE_STEP, **kwargs):
    return run_test(device, model, "t1", goal, "com.example", tmp_path, **kwargs)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("vision_test_agent.agent.time.sleep", lambda _: None)


def _actions(device):
    return [call for call in device.calls if call[0] != "launch"]


def test_parse_splits_steps_and_expected_result():
    assert parse_test_case("Tap A; Enter B. Expected: C shows.") == (["Tap A", "Enter B"], "C shows.")
    assert parse_test_case("Expected: C shows.") == ([], "C shows.")


@pytest.mark.parametrize("text", ["Tap A", "Tap A. Expected:   "])
def test_parse_requires_an_expected_result(text):
    with pytest.raises(ValueError, match="expected result"):
        parse_test_case(text)


def test_steps_run_in_order_then_verdict_is_asked_once(tmp_path):
    device = FakeDevice()
    model = ScriptedModel([_step("type", 2, "Buy milk"), _step("tap", 1)], [_verdict("pass", 3)])
    result = _run(device, model, tmp_path, goal=GOAL, bug="save_lost")
    assert device.calls == [("launch", "com.example", "save_lost"), ("type", "Title", "Buy milk"), ("tap", "Save")]
    assert result.verdict == "pass" and result.evidence == ["Buy milk"]
    assert [step.action for step in result.steps] == ["type", "tap", "judge"]
    assert "Step: Enter Buy milk" in model.step_prompts[0] and "Step: Tap Save" in model.step_prompts[1]
    assert "Expected result: Buy milk is listed." in model.verdict_prompts[0]
    assert (tmp_path / "screen_01.png").read_bytes() == b"png"


def test_test_case_with_no_steps_only_judges(tmp_path):
    model = ScriptedModel([], [_verdict("fail", 3)])
    result = _run(FakeDevice(), model, tmp_path, goal="Expected: titles are readable.")
    assert result.verdict == "fail" and model.step_prompts == []


def test_invented_element_is_refused_and_model_is_told(tmp_path):
    device = FakeDevice()
    model = ScriptedModel([_step("tap", 99), _step("tap", 1)], [_verdict("pass", 3)])
    _run(device, model, tmp_path)
    assert _actions(device) == [("tap", "Save")]
    assert "element 99 is not on the screen" in model.step_prompts[1]


def test_label_that_does_not_match_the_number_is_refused(tmp_path):
    device = FakeDevice()
    model = ScriptedModel([_step("tap", 2, label="Save"), _step("tap", 1)], [_verdict("pass", 3)])
    _run(device, model, tmp_path)
    assert _actions(device) == [("tap", "Save")]
    assert "labelled 'Title', not 'Save'" in model.step_prompts[1]


@pytest.mark.parametrize(
    "bad, message",
    [
        (_step("tap", 3), "cannot be tapped"),
        (_step("type", 1, "x"), "cannot take text"),
        (_step("type", 2), "no text was given"),
    ],
)
def test_impossible_uses_of_an_element_are_refused(tmp_path, bad, message):
    device = FakeDevice()
    model = ScriptedModel([bad, _step("tap", 1)], [_verdict("pass", 3)])
    _run(device, model, tmp_path)
    assert _actions(device) == [("tap", "Save")] and message in model.step_prompts[1]


def test_impossible_step_fails_the_test_and_shows_the_screen(tmp_path):
    result = _run(FakeDevice(), ScriptedModel([_step("impossible")], [_verdict("pass", 3)]), tmp_path)
    assert result.verdict == "fail" and "Could not do step 'Tap Save'" in result.explanation
    assert result.evidence == ["Save", "Title", "Buy milk"]


def test_scroll_then_act_is_one_test_step(tmp_path):
    device = FakeDevice()
    _run(device, ScriptedModel([_step("scroll"), _step("tap", 1)], [_verdict("pass", 3)]), tmp_path)
    assert _actions(device) == [("scroll",), ("tap", "Save")]


def test_scrolling_forever_fails_the_step(tmp_path):
    result = _run(FakeDevice(), ScriptedModel([_step("scroll")], [_verdict("pass", 3)]), tmp_path)
    assert result.verdict == "fail" and "even after scrolling" in result.explanation


@pytest.mark.parametrize("verdict", ["pass", "fail"])
def test_verdict_without_real_evidence_becomes_unsure(tmp_path, verdict):
    model = ScriptedModel([_step("tap", 1)], [_verdict(verdict, 42)])
    result = _run(FakeDevice(), model, tmp_path)
    assert result.verdict == "unsure" and result.rejected_evidence == 3
    assert "needs at least one number from 1 to 3" in model.verdict_prompts[1]


def test_model_can_correct_missing_evidence(tmp_path):
    model = ScriptedModel([_step("tap", 1)], [_verdict("pass"), _verdict("pass", 3)])
    result = _run(FakeDevice(), model, tmp_path)
    assert result.verdict == "pass" and result.evidence == ["Buy milk"]


def test_crash_decides_without_asking_the_model(tmp_path):
    model = ScriptedModel([_step("tap", 1)], [_verdict("pass", 3)])
    result = _run(FakeDevice(crash_after=1), model, tmp_path)
    assert result.verdict == "fail" and result.found_by == "program"
    assert "FATAL EXCEPTION" in result.evidence[0] and model.verdict_prompts == []


def test_not_responding_dialog_counts_as_failure(tmp_path):
    device = FakeDevice(focus="mCurrentFocus=Window{1 u0 Application Not Responding: com.example}")
    result = _run(device, ScriptedModel([_step("tap", 1)], [_verdict("pass", 3)]), tmp_path)
    assert result.verdict == "fail" and result.found_by == "program"


def test_repeated_model_errors_end_as_unsure(tmp_path):
    result = _run(FakeDevice(), ScriptedModel([ModelError("Could not reach Ollama")]), tmp_path)
    assert result.verdict == "unsure" and "Could not reach Ollama" in result.explanation


def test_unreadable_screen_ends_as_unsure(tmp_path):
    result = _run(FakeDevice(unreadable=True), ScriptedModel([_step("tap", 1)]), tmp_path)
    assert result.verdict == "unsure" and "could not be read" in result.explanation


def test_screenshot_can_be_withheld(tmp_path):
    model = ScriptedModel([_step("tap", 1)], [_verdict("pass", 3)])
    _run(FakeDevice(), model, tmp_path, use_screenshot=False)
    assert model.images == [None, None]


def test_parse_elements_numbers_what_matters():
    xml = """<hierarchy>
      <node class="android.widget.FrameLayout" text="" content-desc="" clickable="false" bounds="[0,0][100,100]">
        <node class="android.widget.Button" text="Save" clickable="true" checkable="false" bounds="[0,0][100,50]"/>
        <node class="android.widget.EditText" text="" hint="Title" clickable="true" checkable="false" bounds="[0,60][100,100]"/>
        <node class="android.widget.Switch" text="Show count" clickable="true" checkable="true" checked="true" bounds="[0,100][100,200]"/>
        <node class="android.view.View" text="" clickable="true" bounds="[0,0][1,1]"/>
      </node>
    </hierarchy>"""
    save_button, box, switch = parse_elements(xml)
    assert (save_button.number, save_button.label, save_button.x, save_button.y) == (1, "Save", 50, 25)
    assert box.typable and box.label == "Title"
    assert switch.switched_on is True and "[tap, on]" in switch.describe()


def _failed(tmp_path):
    return _run(FakeDevice(), ScriptedModel([_step("tap", 1)], [_verdict("fail", 3)]), tmp_path)


def test_report_shows_steps_screenshots_and_evidence(tmp_path):
    markdown = to_markdown(_failed(tmp_path))
    assert "t1: FAIL" in markdown and "`Buy milk`" in markdown and "screen_01.png" in markdown
    assert "Tap Save" in markdown


def test_save_writes_bug_report_only_for_failures(tmp_path):
    failed = _failed(tmp_path / "a")
    save(failed, tmp_path / "a", b"log line\n")
    assert (tmp_path / "a" / "device.log").read_bytes() == b"log line\n"
    assert json.loads((tmp_path / "a" / "jira_fields.json").read_text("utf-8"))["issuetype"] == {"name": "Bug"}

    passed = _run(FakeDevice(), ScriptedModel([_step("tap", 1)], [_verdict("pass", 3)]), tmp_path / "b")
    save(passed, tmp_path / "b")
    assert (tmp_path / "b" / "report.md").exists() and not (tmp_path / "b" / "jira_fields.json").exists()


def test_jira_fields_quote_the_evidence(tmp_path):
    fields = to_jira_fields(_failed(tmp_path), "QA")
    assert fields["project"] == {"key": "QA"} and "Buy milk" in fields["description"]
