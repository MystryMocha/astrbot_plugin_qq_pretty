"""Turn Markdown into QQ-readable plain text.

QQ (NapCat / personal client) does not render Markdown. Gemini still emits
**, # headings, tables and fences; this module rewrites them into something
you can actually read on a phone.
"""

from __future__ import annotations

import re
from typing import Literal

HeadingStyle = Literal["bracket", "plain"]
CodeBlockStyle = Literal["label", "indent", "keep"]

_FENCE_RE = re.compile(r"```([^\n`]*)\n?([\s\S]*?)```")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_HR_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$", re.MULTILINE)
_UL_RE = re.compile(r"^(\s*)[-*+]\s+(\[[ xX]\]\s+)?(.*)$", re.MULTILINE)
_BOLD_ITALIC_RE = re.compile(r"\*\*\*(.+?)\*\*\*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(
    r"(?<![A-Za-z0-9])\*(?![A-Za-z0-9\s*])([^*\n]+?)(?<![A-Za-z0-9\s])\*(?![A-Za-z0-9])"
)
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?(?:b|i|em|strong|p|div|span|h[1-6])\s*/?>", re.IGNORECASE)
_EXTRA_BLANK_RE = re.compile(r"\n{3,}")
_TRAIL_WS_RE = re.compile(r"[ \t]+\n")

_CODE_TOKEN = "\x00QQMDCODE{0}\x00"
_INLINE_TOKEN = "\x00QQMDINLINE{0}\x00"


def looks_like_markdown(text: str) -> bool:
    if not text:
        return False
    if "```" in text or "**" in text or "~~" in text:
        return True
    if "`" in text:
        return True
    if re.search(r"^#{1,6}\s+\S", text, re.MULTILINE):
        return True
    if re.search(r"^\s{0,3}[-*+]\s+\S", text, re.MULTILINE):
        return True
    if re.search(r"^\|.+\|", text, re.MULTILINE):
        return True
    if re.search(r"^\s*>\s+\S", text, re.MULTILINE):
        return True
    if re.search(r"\[[^\]]+\]\([^)]+\)", text):
        return True
    if re.search(r"^(?:-{3,}|\*{3,}|_{3,})$", text, re.MULTILINE):
        return True
    return False


def convert(
    text: str,
    *,
    heading_style: HeadingStyle = "bracket",
    code_block_style: CodeBlockStyle = "label",
    keep_inline_code: bool = False,
) -> str:
    if not text or not looks_like_markdown(text):
        return text

    fences: list[tuple[str, str]] = []
    inlines: list[str] = []

    def _stash_fence(match: re.Match[str]) -> str:
        lang = (match.group(1) or "").strip()
        body = (match.group(2) or "").rstrip("\n")
        fences.append((lang, body))
        return _CODE_TOKEN.format(len(fences) - 1)

    work = _FENCE_RE.sub(_stash_fence, text)

    def _stash_inline(match: re.Match[str]) -> str:
        inlines.append(match.group(1))
        return _INLINE_TOKEN.format(len(inlines) - 1)

    work = _INLINE_CODE_RE.sub(_stash_inline, work)
    work = _convert_tables(work)
    work = _HEADING_RE.sub(lambda m: _heading(m, heading_style), work)
    work = _IMAGE_RE.sub(_image, work)
    work = _LINK_RE.sub(_link, work)
    work = _HR_RE.sub("", work)
    work = _BLOCKQUOTE_RE.sub(lambda m: f"｜{m.group(1)}", work)
    work = _UL_RE.sub(_unordered, work)
    work = _BOLD_ITALIC_RE.sub(r"\1", work)
    work = _BOLD_RE.sub(r"\1", work)
    work = _STRIKE_RE.sub(r"\1", work)
    work = _ITALIC_RE.sub(r"\1", work)
    work = _HTML_BR_RE.sub("\n", work)
    work = _HTML_TAG_RE.sub("", work)

    for i, raw in enumerate(inlines):
        token = _INLINE_TOKEN.format(i)
        work = work.replace(token, f"`{raw}`" if keep_inline_code else raw)

    for i, (lang, body) in enumerate(fences):
        token = _CODE_TOKEN.format(i)
        work = work.replace(token, _format_fence(lang, body, code_block_style))

    work = _TRAIL_WS_RE.sub("\n", work)
    work = _EXTRA_BLANK_RE.sub("\n\n", work)
    return work.strip()


def _heading(match: re.Match[str], style: HeadingStyle) -> str:
    title = match.group(2).strip()
    if not title:
        return ""
    if style == "plain":
        return f"\n{title}\n"
    return f"\n【{title}】\n"


def _image(match: re.Match[str]) -> str:
    alt = (match.group(1) or "").strip()
    url = (match.group(2) or "").strip()
    if alt and url and alt != url:
        return f"{alt} {url}"
    return alt or url


def _link(match: re.Match[str]) -> str:
    label = (match.group(1) or "").strip()
    url = (match.group(2) or "").strip()
    if not url:
        return label
    if not label or label == url:
        return url
    return f"{label} {url}"


def _unordered(match: re.Match[str]) -> str:
    indent = match.group(1) or ""
    checkbox = match.group(2)
    body = match.group(3) or ""
    depth = len(indent.replace("\t", "    ")) // 2
    prefix = "  " * depth
    if checkbox:
        mark = "✓ " if checkbox.strip().lower() == "[x]" else "□ "
        return f"{prefix}{mark}{body}"
    return f"{prefix}· {body}"


def _format_fence(lang: str, body: str, style: CodeBlockStyle) -> str:
    if style == "keep":
        header = f"```{lang}\n" if lang else "```\n"
        return f"\n{header}{body}\n```\n"
    if style == "indent":
        indented = "\n".join(f"  {line}" if line else "" for line in body.split("\n"))
        return f"\n{indented}\n"
    label = lang.strip() if lang.strip() else "代码"
    return f"\n【{label}】\n{body}\n"


def _is_sep_cell(cell: str) -> bool:
    return bool(re.fullmatch(r":?-{2,}:?", cell.strip().replace(" ", "")))


def _split_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if not cells or all(c == "" for c in cells):
        return None
    return cells


def _convert_tables(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        row = _split_row(lines[i])
        if row is None:
            out.append(lines[i])
            i += 1
            continue
        block = [row]
        j = i + 1
        while j < n:
            nxt = _split_row(lines[j])
            if nxt is None:
                break
            block.append(nxt)
            j += 1
        if len(block) == 1:
            out.append(lines[i])
            i += 1
            continue
        sep_idx = None
        for k, cells in enumerate(block):
            if cells and all(_is_sep_cell(c) for c in cells):
                sep_idx = k
                break
        if sep_idx is None and len(block) < 2:
            out.extend(lines[i:j])
            i = j
            continue
        header: list[str] = []
        data_rows: list[list[str]]
        if sep_idx == 1:
            header = block[0]
            data_rows = block[2:]
        elif sep_idx == 0:
            data_rows = block[1:]
        else:
            data_rows = [r for k, r in enumerate(block) if k != sep_idx]
            if data_rows:
                header = data_rows[0]
                data_rows = data_rows[1:]
        rendered = _render_table(header, data_rows)
        if rendered:
            if out and out[-1].strip():
                out.append("")
            out.append(rendered)
            if j < n and lines[j].strip():
                out.append("")
        else:
            out.extend(lines[i:j])
        i = j
    return "\n".join(out)


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    if not rows and not header:
        return ""
    cols = 0
    if header:
        cols = max(cols, len(header))
    for row in rows:
        cols = max(cols, len(row))
    if cols == 0:
        return ""

    def cell(row: list[str], idx: int) -> str:
        return row[idx].strip() if idx < len(row) else ""

    lines: list[str] = []
    if cols == 2 and header:
        key_h, val_h = cell(header, 0), cell(header, 1)
        for row in rows:
            k, v = cell(row, 0), cell(row, 1)
            if not k and not v:
                continue
            if k and v:
                lines.append(f"{k}：{v}")
            else:
                lines.append(k or v)
        if not lines and (key_h or val_h):
            lines.append(f"{key_h}：{val_h}" if key_h and val_h else (key_h or val_h))
        return "\n".join(lines)

    if header:
        heads = [cell(header, i) for i in range(cols) if cell(header, i)]
        if not rows:
            return " · ".join(heads)
        for row in rows:
            parts: list[str] = []
            for i in range(cols):
                h = cell(header, i)
                v = cell(row, i)
                if not v:
                    continue
                parts.append(f"{h}：{v}" if h else v)
            if parts:
                lines.append(" · ".join(parts))
        return "\n".join(lines)

    for row in rows:
        vals = [cell(row, i) for i in range(cols) if cell(row, i)]
        if vals:
            lines.append(" · ".join(vals))
    return "\n".join(lines)
