import base64
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

from vision_test_agent.model import DEFAULT_MODEL, ModelError, OllamaModel, shrink

sys.path.insert(0, str(Path(__file__).parent.parent / "eval"))
from run_eval import AFFECTS, GOALS, SPLITS, cases, score, wilson_interval  # noqa: E402


def _png(width=1080, height=2400):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, format="PNG")
    return output.getvalue()


def _opener(content, captured=None):
    def opener(request, timeout):
        if captured is not None:
            captured.update(json.loads(request.data), url=request.full_url)
        return io.BytesIO(json.dumps({"message": {"content": content}}).encode())

    return opener


GOOD = json.dumps({"reason": "r", "action": "tap", "element_label": "Save", "element": 1, "text": ""})


def test_request_stays_local_and_includes_a_shrunken_image(monkeypatch):
    monkeypatch.delenv("VTEST_MODEL", raising=False)
    captured = {}
    action = OllamaModel(opener=_opener(GOOD, captured)).choose_step("PROMPT", _png())
    assert captured["url"].startswith("http://localhost:") and captured["model"] == DEFAULT_MODEL
    assert captured["options"]["temperature"] == 0 and captured["messages"][1]["content"] == "PROMPT"
    image = Image.open(io.BytesIO(base64.b64decode(captured["messages"][1]["images"][0])))
    assert image.height == 1280 and image.format == "JPEG"
    assert action.action == "tap"


def test_no_screenshot_sends_no_image():
    captured = {}
    OllamaModel(opener=_opener(GOOD, captured)).choose_step("P", None)
    assert "images" not in captured["messages"][1]


def test_small_image_keeps_its_size():
    assert Image.open(io.BytesIO(shrink(_png(100, 200)))).size == (100, 200)


def test_action_outside_the_fixed_list_is_rejected():
    bad = json.dumps({"reason": "r", "action": "shell", "element_label": "", "element": 0, "text": "rm -rf /"})
    with pytest.raises(ModelError, match="invalid answer"):
        OllamaModel(opener=_opener(bad)).choose_step("P", None)


def test_ollama_down_gives_a_clear_error():
    def refused(request, timeout):
        raise urllib.error.URLError("refused")

    with pytest.raises(ModelError, match="Could not reach Ollama"):
        OllamaModel(opener=refused).choose_step("P", None)


def test_missing_model_suggests_pull():
    def missing(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"not found"))

    with pytest.raises(ModelError, match="ollama pull"):
        OllamaModel(opener=missing).choose_step("P", None)


def test_dev_and_test_use_different_bugs():
    assert not set(SPLITS["dev"]["bugs"]) & set(SPLITS["test"]["bugs"])
    assert set(SPLITS["dev"]["bugs"]) | set(SPLITS["test"]["bugs"]) == set(AFFECTS)


@pytest.mark.parametrize("split", ["dev", "test"])
def test_case_expectations_follow_the_bug_table(split):
    for case in cases(split):
        assert case["test"] in GOALS
        should_fail = case["bug"] != "" and case["test"] in AFFECTS[case["bug"]]
        assert case["expected"] == ("fail" if should_fail else "pass")


def _row(expected, verdict, found_by="model", steps=2, seconds=10.0):
    return dict(expected=expected, verdict=verdict, found_by=found_by, steps=steps, seconds=seconds, rejected_evidence=0)


def test_score_on_hand_checked_example():
    rows = [
        _row("fail", "fail", found_by="program"),
        _row("fail", "pass"),
        _row("fail", "unsure"),
        _row("pass", "pass"),
        _row("pass", "fail"),
    ]
    result = score(rows)
    assert result["bugs_detected"] == 1 and result["detection_rate"] == 0.333
    assert result["detected_by_crash_check"] == 1
    assert result["false_alarms"] == 1 and result["false_alarm_rate"] == 0.5
    assert result["unsure"] == 1 and result["correct"] == 2


def test_wilson_interval_known_value():
    assert wilson_interval(8, 10) == [0.49, 0.943]
