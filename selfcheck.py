"""The admin command's framework-free half: hit counts kept in plugin_data, the command's words and replies.

`/outbound_guard` (alias `/防泄密`) shows the check's state and how often each rule hit; `test <text>` says
which rules a text would hit and what the current mode would do, without repeating any of the text; `reset`
starts the counts again. Counts come from real outbound checks only, never from a test, and hold rule names
and numbers only: the masked samples stay in the log lines.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Iterable, Mapping

VERSION = 1
SOURCES = ("reply", "send")
ACTIONS = ("logged", "masked", "held")
MAX_RULES = 64  # rule names come from the code, so this only bounds a hand-edited file
MAX_RULE_NAME = 40

COMMAND_NAMES = ("outbound_guard", "防泄密")
_ACTION_WORDS = {
    "状态": "status", "status": "status",
    "测试": "test", "test": "test",
    "清零": "reset", "reset": "reset",
}

RULE_LABELS = {
    "known_secret": "已知密钥",
    "config_secret": "AstrBot 配置里的密钥",
    "private_key": "私钥",
    "token_prefix": "服务令牌",
    "bot_token": "机器人令牌",
    "jwt": "JWT 令牌",
    "assigned_secret": "带赋值的凭据",
    "url_credentials": "带账号密码的网址",
    "server_path": "服务器路径",
    "windows_user_path": "Windows 用户目录",
    "internal_url": "内部地址",
    "internal_name": "内部名称",
}
MODE_LABELS = {
    "off": "关闭，不检查",
    "log": "只记日志，消息照发",
    "mask": "命中的片段打码后发送",
    "drop": "命中就不发",
}
SOURCE_LABELS = {"reply": "回复", "send": "直接发送"}
ACTION_LABELS = {"logged": "只记日志", "masked": "打码", "held": "拦下"}

USAGE = "\n".join((
    "用法（仅管理员）：",
    "/防泄密：看检查状态和命中统计",
    "/防泄密 测试 <文本>：看这段文字会命中哪些规则，不复述原文",
    "/防泄密 清零：命中统计从现在重新算",
))
TEST_USAGE = ("用法：/防泄密 测试 <文本>\n只回答命中了哪些规则，不复述原文。测试文字和普通消息一样会进聊天记录，"
              "建议私聊机器人，用同样格式的假值。")


def parse_command(message: str) -> tuple[str, str]:
    """(action, rest of the text) from the raw message: `status`, `test`, `reset`, or `help`.

    The framework may or may not leave the command word and its `/` in the message; both are read. The rest
    keeps its spaces and line breaks, since a PEM header or a config line is tested as written.
    """
    text = str(message or "").strip()
    head, rest = _split(text)
    if head.lstrip("/").lower() in COMMAND_NAMES:
        head, rest = _split(rest)
    if not head:
        return "status", ""
    return _ACTION_WORDS.get(head.lower(), "help"), rest


def _split(text: str) -> tuple[str, str]:
    parts = text.split(None, 1)
    if not parts:
        return "", ""
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value) if value > 0 else 0


def _stamp(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value) if value > 0 else 0.0


class HitStats:
    """Hits per source (`reply`, `send`), per action and per rule, since a start time, saved as JSON."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time):
        self.path = path
        self.clock = clock
        self.load_error = ""
        self.reset()
        self.dirty = False

    def reset(self) -> None:
        self.since = self.clock()
        self.events = dict.fromkeys(SOURCES, 0)
        self.actions = dict.fromkeys(ACTIONS, 0)
        self.rules: dict[str, dict] = {}
        self.dirty = True

    def record(self, source: str, rules: Iterable[str], action: str) -> None:
        """One checked message that hit: counted once per source and action, once per rule it hit."""
        if source not in SOURCES or action not in ACTIONS:
            return
        now = self.clock()
        self.events[source] += 1
        self.actions[action] += 1
        for rule in dict.fromkeys(str(r)[:MAX_RULE_NAME] for r in rules if r):
            entry = self.rules.get(rule)
            if entry is None:
                if len(self.rules) >= MAX_RULES:
                    continue
                entry = self.rules[rule] = {**dict.fromkeys(SOURCES, 0), "last": 0.0}
            entry[source] += 1
            entry["last"] = now
        self.dirty = True

    @property
    def total(self) -> int:
        return sum(self.events.values())

    def to_dict(self) -> dict:
        return {"version": VERSION, "since": self.since, "events": dict(self.events),
                "actions": dict(self.actions), "rules": {k: dict(v) for k, v in self.rules.items()}}

    def _absorb(self, data: Mapping) -> None:
        since = _stamp(data.get("since"))
        if since:
            self.since = since
        events = data.get("events") if isinstance(data.get("events"), Mapping) else {}
        actions = data.get("actions") if isinstance(data.get("actions"), Mapping) else {}
        self.events = {s: _count(events.get(s)) for s in SOURCES}
        self.actions = {a: _count(actions.get(a)) for a in ACTIONS}
        rules = data.get("rules") if isinstance(data.get("rules"), Mapping) else {}
        self.rules = {}
        for name, entry in rules.items():
            if len(self.rules) >= MAX_RULES:
                break
            if not isinstance(name, str) or not name or not isinstance(entry, Mapping):
                continue
            self.rules[name[:MAX_RULE_NAME]] = {**{s: _count(entry.get(s)) for s in SOURCES},
                                                "last": _stamp(entry.get("last"))}

    @classmethod
    def load(cls, path: Path, clock: Callable[[], float] = time.time) -> "HitStats":
        """The saved counts, or fresh ones. A missing or broken file is (re)written at the next save, so the
        start time survives a reload; an unreadable one is left alone and named in `load_error`."""
        stats = cls(path, clock)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            stats.dirty = True
            return stats
        except OSError as exc:
            stats.load_error = type(exc).__name__
            return stats
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeError) as exc:
            stats.load_error = type(exc).__name__
            stats.dirty = True
            return stats
        stats._absorb(data)
        return stats

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(json.dumps(self.to_dict(), ensure_ascii=False, indent=1).encode("utf-8"))
        os.replace(tmp, self.path)
        self.dirty = False


# ---- replies -----------------------------------------------------------------------------------------


def _when(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def rule_line(rule: str) -> str:
    label = RULE_LABELS.get(rule)
    return f"{label} {rule}" if label else rule


def stats_lines(stats: HitStats) -> list[str]:
    head = f"命中统计（{_when(stats.since)} 起）："
    if not stats.total:
        return [head + "还没有命中。"]
    sources = "、".join(f"{SOURCE_LABELS[s]} {stats.events[s]} 次" for s in SOURCES if stats.events[s])
    actions = "、".join(f"{ACTION_LABELS[a]} {stats.actions[a]} 次" for a in ACTIONS if stats.actions[a])
    lines = [f"{head}{sources}；{actions}"]
    ranked = sorted(stats.rules.items(), key=lambda item: (-sum(item[1][s] for s in SOURCES), item[0]))
    for rule, entry in ranked:
        counts = "、".join(f"{SOURCE_LABELS[s]} {entry[s]}" for s in SOURCES if entry[s])
        last = f"，最近 {_when(entry['last'])}" if entry["last"] else ""
        lines.append(f"· {rule_line(rule)}：{counts}{last}")
    lines.append("打码样本只在日志里：qq-pretty: outbound hit / send-layer hit，按时间去找。")
    return lines


def status_text(mode: str, gate: str, config_secrets: int | None, lists: Mapping[str, int],
                stats: HitStats) -> str:
    """The overview: what is checked, with what, and how often each rule hit. No list entry, no value."""
    lines = [
        "防泄密 · 自检",
        f"模式：{mode}（{MODE_LABELS.get(mode, '')}）",
        f"也查直接发送：{gate}",
        "AstrBot 配置里的密钥：" + ("不保护" if config_secrets is None else f"保护 {config_secrets} 个"),
        (f"已知密钥 {lists.get('known', 0)} 个；内部主机 {lists.get('hosts', 0)} 个、"
         f"私有路径 {lists.get('paths', 0)} 个、内部名称 {lists.get('names', 0)} 个"),
        "",
        *stats_lines(stats),
        "",
        USAGE,
    ]
    return "\n".join(lines)


def describe_test(rules: list[str], mode: str, spans: int, mask_left: bool, mask_text: str,
                  has_notice: bool) -> str:
    """Which rules a test text hit and what the current mode would do with it. Never any of the text."""
    if not rules:
        line = "测试：没有命中任何规则，这段会照常发出。"
        return line + ("（出站检查现在关着。）" if mode == "off" else "")
    lines = [f"测试：命中 {len(rules)} 条规则（不复述原文）", *(f"· {rule_line(r)}" for r in rules)]
    if mode == "off":
        lines.append("现在模式是 off，发送时不会检查。")
    elif mode == "log":
        lines.append("按现在的模式 log：照常发出，日志里记一笔。")
    elif mode == "mask" and not mask_left:
        lines.append(f"按现在的模式 mask：会把 {spans} 处换成「{mask_text}」后发出。")
    else:
        why = "打码后仍有残留，会按 drop 处理：" if mode == "mask" else "按现在的模式 drop："
        lines.append(why + ("整条不发，改发拦截提示。" if has_notice else "整条不发，聊天里什么也不发。"))
    return "\n".join(lines)


def reset_text(stats: HitStats) -> str:
    return f"命中统计已清零，从 {_when(stats.since)} 重新算。"
