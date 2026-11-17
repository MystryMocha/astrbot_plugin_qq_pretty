from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from convert import convert, looks_like_markdown


def test_plain_chat_untouched():
    text = "哼，本小姐才没有在意你。"
    assert convert(text) == text
    assert looks_like_markdown(text) is False


def test_math_asterisk_untouched():
    text = "3 * 4 = 12，再乘一次是 2*3*4。"
    assert convert(text) == text


def test_dunder_filename_untouched():
    text = "改 __init__.py 就行。"
    assert convert(text) == text
    assert looks_like_markdown(text) is False


def test_bold_and_heading():
    src = "**好的，本小姐来了。**\n\n### 结论\n\n先改配置，再重启。"
    out = convert(src)
    assert "**" not in out
    assert "###" not in out
    assert "【结论】" in out
    assert "好的，本小姐来了。" in out
    assert "先改配置，再重启。" in out


def test_heading_plain_style():
    out = convert("## 步骤\n去做。", heading_style="plain")
    assert "【步骤】" not in out
    assert "步骤" in out
    assert "去做。" in out


def test_bullet_list():
    src = "可以这样：\n- 先备份\n- 再改\n  - 子项"
    out = convert(src)
    assert "· 先备份" in out
    assert "· 再改" in out
    assert "· 子项" in out
    assert "- 先备份" not in out


def test_task_list():
    src = "- [x] 做完了\n- [ ] 还没做"
    out = convert(src)
    assert "✓ 做完了" in out
    assert "□ 还没做" in out


def test_two_column_table():
    src = """| 名称 | 作用 |
|------|------|
| FTS | 全文检索 |
| 向量 | 语义召回 |"""
    out = convert(src)
    assert "FTS：全文检索" in out
    assert "向量：语义召回" in out
    assert "|" not in out
    assert "---" not in out


def test_wide_table():
    src = """| 群 | 人 | 条数 |
|---|---|---|
| A | 小明 | 12 |
| B | 小红 | 7 |"""
    out = convert(src)
    assert "群：A" in out
    assert "人：小明" in out
    assert "条数：12" in out
    assert "|" not in out


def test_code_block_label():
    src = "看这段：\n```python\nprint(1)\n```\n完。"
    out = convert(src)
    assert "【python】" in out
    assert "print(1)" in out
    assert "```" not in out
    assert "完。" in out


def test_code_block_keep():
    src = "```\nx = 1\n```"
    out = convert(src, code_block_style="keep")
    assert "```" in out
    assert "x = 1" in out


def test_code_block_not_stripped_inside():
    src = "```\n**not bold**\n# not heading\n```"
    out = convert(src)
    assert "**not bold**" in out
    assert "# not heading" in out


def test_inline_code_stripped_by_default():
    out = convert("用 `pip install foo` 就行")
    assert out == "用 pip install foo 就行"


def test_inline_code_kept():
    out = convert("用 `pip install foo` 就行", keep_inline_code=True)
    assert "`pip install foo`" in out


def test_link_and_image():
    src = "看[文档](https://example.com/a) 和 ![图](https://example.com/b.png)"
    out = convert(src)
    assert "文档 https://example.com/a" in out
    assert "图 https://example.com/b.png" in out
    assert "[" not in out


def test_blockquote_and_hr():
    src = "> 先别急\n\n---\n\n然后继续"
    out = convert(src)
    assert "｜先别急" in out
    assert "---" not in out
    assert "然后继续" in out


def test_strike_and_italic():
    src = "这是 ~~旧的~~，还有 *轻轻* 一下"
    out = convert(src)
    assert "旧的" in out
    assert "轻轻" in out
    assert "~~" not in out
    assert "*" not in out


def test_numbered_list_with_bold():
    src = "1. **安装**：pip install x\n2. **运行**：python y"
    out = convert(src)
    assert "1. 安装：pip install x" in out
    assert "2. 运行：python y" in out
    assert "**" not in out


def test_pipe_in_prose_not_table():
    src = "进度 50% | 还早呢\n下一项也一样 | 别急"
    assert convert(src) == src


def test_gemini_typical_reply():
    src = """**行吧，本小姐看一眼。**

### 原因

配置写错了。

| 项 | 值 |
|---|---|
| t2i | 关 |
| 平台 | QQ |

改完重启。

```bash
docker compose restart astrbot
```
"""
    out = convert(src)
    assert "**" not in out
    assert "###" not in out
    assert "【原因】" in out
    assert "t2i：关" in out
    assert "平台：QQ" in out
    assert "【bash】" in out
    assert "docker compose restart astrbot" in out
    assert "行吧，本小姐看一眼。" in out
