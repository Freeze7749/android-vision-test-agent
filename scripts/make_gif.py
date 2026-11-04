"""Turn the real screenshots of one test run into a GIF, with each step written underneath.

    python scripts/make_gif.py out/my_run docs/demo.gif
"""
import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT, CAPTION = 360, 800, 150


def frame(image_file: Path, caption: str, color: str) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT + CAPTION), color)
    canvas.paste(Image.open(image_file).convert("RGB").resize((WIDTH, HEIGHT)), (0, 0))
    font = ImageFont.load_default(size=18)
    wrapped = "\n".join(textwrap.wrap(caption, width=36)[:6])
    ImageDraw.Draw(canvas).multiline_text((12, HEIGHT + 10), wrapped, fill="white", font=font)
    return canvas


def main(run_dir: str, out_file: str) -> None:
    folder = Path(run_dir)
    result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    frames, durations = [], []
    for step in result["steps"]:
        caption = f"Step {step['number']}: {step['instruction']}"
        if step["target"]:
            caption += f" -> {step['action']} '{step['target']}'"
        frames.append(frame(folder / step["screenshot"], caption, "#202830"))
        durations.append(1800)
    color = {"pass": "#1B6E3C", "fail": "#A32020"}.get(result["verdict"], "#7A5B00")
    summary = f"{result['verdict'].upper()}. Evidence: {', '.join(result['evidence'])[:150]}"
    frames.append(frame(folder / result["steps"][-1]["screenshot"], summary, color))
    durations.append(4000)
    frames[0].save(out_file, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print(f"{out_file}: {Path(out_file).stat().st_size / 1024:.0f} KB, {len(frames)} frames")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
