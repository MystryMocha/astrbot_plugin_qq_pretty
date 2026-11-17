"""Render the README preview images (preview.en.png, preview.zh-CN.png).

The texts are not typed in by hand: the left panel is the README example input,
the right panel is what convert() makes of it, and the bottom strip is what
sensitive.redact() makes of a made-up key. Rerun this after changing the README
example or the converter:

    python assets/make_preview.py [--browser PATH]

Needs Pillow and a Chromium-based browser (Edge or Chrome) for the screenshot.
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
sys.path.insert(0, str(PLUGIN))
from convert import convert  # noqa: E402
import sensitive  # noqa: E402

KEY = "sk-" + "EXAMPLE-not-a-real-key-000000"  # split so secret scanners skip this made-up key
TEXT = {
    "en": dict(
        readme="README.md", pill="AstrBot plugin · OneBot v11",
        title="QQ Formatter & Outbound Guard",
        sub="One model reply, as QQ shows it without and with the plugin",
        before="Without the plugin", after="With the plugin", bot="Bot",
        guard="Outbound check in mask mode", wrote="Model wrote", sent="Sent",
        leak=f'The config is at /data/astrbot/data/cmd_config.json, api_key = "{KEY}"',
    ),
    "zh-CN": dict(
        readme="README.zh-CN.md", pill="AstrBot 插件 · OneBot v11",
        title="QQ 排版与防泄密",
        sub="同一条模型回复，不装插件和装了插件，在 QQ 上的样子",
        before="不装插件", after="装了插件", bot="机器人",
        guard="出站检查（mask 模式）", wrote="模型写的", sent="实际发出",
        leak=f'配置在 /data/astrbot/data/cmd_config.json，api_key = "{KEY}"',
    ),
}
CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
NAMES = ["msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"]

CSS = """
*{box-sizing:border-box}
html,body{margin:0;background:transparent}
.card{width:1200px;border-radius:28px;overflow:hidden;padding:34px 44px 40px;color:#0f172a;
 font-family:"Segoe UI","Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
 background:radial-gradient(900px 500px at 0% 0%,#e0e7ff 0,transparent 65%),
            radial-gradient(900px 500px at 100% 100%,#dcfce7 0,transparent 60%),#f8fafc}
.pill{display:inline-block;font-size:13px;font-weight:600;color:#4f46e5;background:#eef2ff;
 border:1px solid #c7d2fe;border-radius:999px;padding:3px 12px}
h1{font-size:30px;line-height:1.25;margin:10px 0 2px;font-weight:700}
.sub{font-size:16px;color:#64748b;margin:0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:22px}
.panel{background:#fff;border-radius:20px;padding:16px 20px 20px;
 box-shadow:0 1px 2px rgba(15,23,42,.06),0 10px 28px rgba(15,23,42,.07)}
.panel h2{font-size:15px;font-weight:600;margin:0 0 12px;display:flex;align-items:center;gap:8px;color:#334155}
.dot{width:10px;height:10px;border-radius:50%}
.bad{background:#f87171}.good{background:#34d399}.info{background:#818cf8}
.chat{display:flex;gap:10px;align-items:flex-start}
.avatar{width:34px;height:34px;border-radius:50%;flex:none;display:grid;place-items:center;
 background:linear-gradient(135deg,#60a5fa,#a78bfa);color:#fff;font-size:12px;font-weight:700}
.name{font-size:12px;color:#94a3b8;margin:0 0 4px 2px}
.bubble{background:#f1f5f9;border-radius:4px 16px 16px 16px;padding:11px 15px;font-size:15px;
 line-height:1.6;white-space:pre-wrap;word-break:break-word}
.wide{grid-column:1/-1}
.rows{display:grid;grid-template-columns:auto 1fr;gap:10px 14px;align-items:center}
.label{font-size:13px;color:#64748b;font-weight:600;white-space:nowrap}
.rows .bubble{border-radius:12px}
mark{border-radius:5px;padding:1px 1px}
mark.hit{background:#fee2e2;color:#b91c1c}
mark.masked{background:#dcfce7;color:#15803d;font-weight:600}
"""


def find_browser(given: str | None) -> str:
    for c in ([given] if given else []) + CANDIDATES:
        if c and Path(c).is_file():
            return c
    for n in NAMES:
        if found := shutil.which(n):
            return found
    sys.exit("No Edge or Chrome found; pass --browser PATH")


def example_input(readme: Path) -> str:
    text = readme.read_text(encoding="utf-8")
    start = text.index("<!-- readme-example:input -->")
    m = re.search(r"^(`{3,})[^\n]*\n(.*?)^\1\s*$", text[start:], re.S | re.M)
    return m.group(2).rstrip("\n")


def marked(text: str, spans, cls: str, replace: str | None = None) -> str:
    out, pos = [], 0
    for a, b in spans:
        out.append(html.escape(text[pos:a]))
        out.append(f'<mark class="{cls}">{html.escape(text[a:b] if replace is None else replace)}</mark>')
        pos = b
    out.append(html.escape(text[pos:]))
    return "".join(out)


def page(lang: str, t: dict) -> str:
    raw = example_input(PLUGIN / t["readme"])
    spans = sensitive.find_spans(t["leak"])
    masked = marked(t["leak"], spans, "masked", sensitive.DEFAULT_MASK)
    if html.unescape(re.sub(r"<[^>]+>", "", masked)) != sensitive.redact(t["leak"]):
        sys.exit("the masked line no longer matches sensitive.redact()")

    def chat(body: str) -> str:
        return (f'<div class="chat"><div class="avatar">AI</div><div><p class="name">{html.escape(t["bot"])}</p>'
                f'<div class="bubble">{html.escape(body)}</div></div></div>')

    e = html.escape
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="card">
<span class="pill">{e(t['pill'])}</span><h1>{e(t['title'])}</h1><p class="sub">{e(t['sub'])}</p>
<div class="grid">
<section class="panel"><h2><span class="dot bad"></span>{e(t['before'])}</h2>{chat(raw)}</section>
<section class="panel"><h2><span class="dot good"></span>{e(t['after'])}</h2>{chat(convert(raw))}</section>
<section class="panel wide"><h2><span class="dot info"></span>{e(t['guard'])}</h2><div class="rows">
<span class="label">{e(t['wrote'])}</span><div class="bubble">{marked(t['leak'], spans, 'hit')}</div>
<span class="label">{e(t['sent'])}</span><div class="bubble">{masked}</div>
</div></section></div></div></body></html>"""


def shoot(browser: str, src: Path, png: Path, work: Path) -> None:
    args = [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--hide-scrollbars", "--force-device-scale-factor=2",
            "--default-background-color=00000000", f"--user-data-dir={work / 'profile'}",
            "--window-size=1200,1600", f"--screenshot={png}", src.as_uri()]
    try:
        subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120, check=True)
    except subprocess.TimeoutExpired:
        sys.exit("The browser did not finish the screenshot. On Windows, Edge started from a virtualenv's "
                 "python.exe has been seen to hang; run this script with the base Python instead.")


def main() -> None:
    from PIL import Image

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--browser", help="path to Edge or Chrome")
    ap.add_argument("--out", type=Path, default=HERE, help="output folder (default: this folder)")
    opts = ap.parse_args()
    browser = find_browser(opts.browser)
    opts.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=opts.out) as tmp:
        work = Path(tmp)
        for lang, t in TEXT.items():
            src, shot = work / f"{lang}.html", work / f"{lang}.png"
            src.write_text(page(lang, t), encoding="utf-8")
            shoot(browser, src, shot, work)
            with Image.open(shot) as im:
                im = im.convert("RGBA")
                box = im.getchannel("A").getbbox()
                if box is None or box[3] >= im.height:
                    sys.exit(f"{lang}: the card does not fit the window or the background is not transparent")
                dest = opts.out / f"preview.{lang}.png"
                im.crop(box).save(dest, optimize=True)
            print(f"{dest.name}: {box[2] - box[0]}x{box[3] - box[1]}, {dest.stat().st_size} bytes")


if __name__ == "__main__":
    main()
