"""The before / after example in both READMEs is real output of convert() with the default settings."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from convert import convert

FENCE = re.compile(r"^(`{3,})[^`\n]*\n(.*?)\n\1$", re.S | re.M)


def block_after(text: str, marker: str) -> str:
    start = text.index(f"<!-- readme-example:{marker} -->")
    found = FENCE.search(text, start)
    assert found, f"no fenced block after the {marker} marker"
    return found.group(2)


@pytest.mark.parametrize("name", ["README.md", "README.zh-CN.md"])
def test_readme_example_matches_convert(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert convert(block_after(text, "input")) == block_after(text, "output")
