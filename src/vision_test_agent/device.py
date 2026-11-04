"""Everything that touches the phone goes through this file, using a fixed set of adb commands."""
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass


class DeviceError(RuntimeError):
    """adb failed or the screen could not be read."""


@dataclass
class Element:
    """One thing on the screen. `number` is what the model uses to refer to it."""

    number: int
    label: str
    x: int
    y: int
    tappable: bool
    typable: bool
    switched_on: bool | None = None

    def describe(self) -> str:
        traits = [t for t, on in (("input box", self.typable), ("tap", self.tappable), ("type", self.typable)) if on]
        if self.switched_on is not None:
            traits.append("on" if self.switched_on else "off")
        return f"{self.number}. {self.label or '(no text)'} [{', '.join(traits)}]" if traits else f"{self.number}. {self.label}"


def _center(bounds: str) -> tuple[int, int]:
    left, top, right, bottom = (int(n) for n in bounds.replace("][", ",").strip("[]").split(","))
    return (left + right) // 2, (top + bottom) // 2


def parse_elements(xml_text: str) -> list[Element]:
    """Turn a `uiautomator dump` into a numbered list of what can be read or used on screen."""
    elements = []
    for node in ET.fromstring(xml_text).iter("node"):
        label = node.get("text") or node.get("content-desc") or node.get("hint") or ""
        tappable = node.get("clickable") == "true"
        typable = "EditText" in (node.get("class") or "")
        if not (label or tappable or typable):
            continue
        if not label and not typable:  # An unlabelled wrapper tells the model nothing.
            continue
        checkable = node.get("checkable") == "true"
        x, y = _center(node.get("bounds"))
        elements.append(
            Element(
                number=len(elements) + 1,
                label=label,
                x=x,
                y=y,
                tappable=tappable,
                typable=typable,
                switched_on=node.get("checked") == "true" if checkable else None,
            )
        )
    return elements


class Device:
    def __init__(self, serial: str | None = None, adb: str | None = None):
        self._adb = [adb or os.environ.get("ADB", "adb")] + (["-s", serial] if serial else [])

    def _run(self, *args: str, timeout: int = 30) -> bytes:
        try:
            done = subprocess.run([*self._adb, *args], capture_output=True, timeout=timeout)
        except FileNotFoundError as error:
            raise DeviceError("adb was not found. Install platform-tools or set the ADB variable.") from error
        except subprocess.TimeoutExpired as error:
            raise DeviceError(f"adb timed out: {' '.join(args)}") from error
        if done.returncode != 0:
            raise DeviceError(f"adb failed: {' '.join(args)}: {done.stderr.decode(errors='replace').strip()}")
        return done.stdout

    def launch(self, package: str, bug: str = "") -> None:
        """Start the app from a clean state, with one seeded bug on if requested."""
        self._run("shell", "pm", "clear", package)
        self._run("logcat", "-c")
        self._run("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        args = ["shell", "am", "start", "-W", "-n", f"{package}/.MainActivity"]
        self._run(*args, *(["--es", "bug", bug] if bug else []))
        time.sleep(1)

    def screenshot(self) -> bytes:
        return self._run("exec-out", "screencap", "-p")

    def elements(self) -> list[Element]:
        self._run("shell", "uiautomator", "dump", "/sdcard/vtest_ui.xml", timeout=25)
        return parse_elements(self._run("exec-out", "cat", "/sdcard/vtest_ui.xml").decode("utf-8", errors="replace"))

    def foreground(self) -> str:
        """The window that has focus: shows whether the app is still open or a crash dialog is up."""
        output = self._run("shell", "dumpsys", "window").decode("utf-8", errors="replace")
        for line in output.splitlines():
            if "mCurrentFocus" in line:
                return line.strip()
        return ""

    def crash_log(self) -> str:
        return self._run("logcat", "-d", "-b", "crash", "-v", "threadtime").decode("utf-8", errors="replace")

    def full_log(self) -> bytes:
        return self._run("logcat", "-d", "-b", "main", "-b", "system", "-b", "crash", "-b", "radio", "-v", "threadtime")

    def tap(self, element: Element) -> None:
        self._run("shell", "input", "tap", str(element.x), str(element.y))

    def type_text(self, element: Element, text: str) -> None:
        self.tap(element)
        time.sleep(0.3)
        # Replace what is in the box, so the result is the same whatever was there.
        self._run("shell", "input", "keyevent", "KEYCODE_MOVE_END")
        self._run("shell", "input", "keycombination", "113", "29")  # Ctrl+A selects all.
        self._run("shell", "input", "keyevent", "KEYCODE_DEL")
        # adb's text command wants %s for a space; quote the rest for the device shell.
        safe = "".join(c for c in text if c.isalnum() or c in " .,_-").replace(" ", "%s")
        if safe:
            self._run("shell", "input", "text", safe)
        self._run("shell", "input", "keyevent", "KEYCODE_ESCAPE")

    def back(self) -> None:
        self._run("shell", "input", "keyevent", "KEYCODE_BACK")

    def scroll_down(self) -> None:
        self._run("shell", "input", "swipe", "540", "1800", "540", "700", "300")
