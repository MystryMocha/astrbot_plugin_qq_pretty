"""Hit counts and the admin command's words and replies, without the framework.
privacy-scan: allow-file (the key header below is a fake test input)"""
from __future__ import annotations

import json

import pytest

from convert import convert
from selfcheck import (USAGE, HitStats, describe_test, parse_command, reset_text, stats_lines,
                       status_text)


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.mark.parametrize("message, expected", [
    ("", ("status", "")),
    ("防泄密", ("status", "")),
    ("/防泄密", ("status", "")),
    ("outbound_guard status", ("status", "")),
    ("防泄密 状态", ("status", "")),
    ("防泄密 清零", ("reset", "")),
    ("/outbound_guard reset", ("reset", "")),
    ("防泄密 测试 ", ("test", "")),
    ("防泄密 乱写一通", ("help", "")),
    ("防泄密　测试　全角空格", ("test", "全角空格")),
    ("测试 前缀已被框架去掉", ("test", "前缀已被框架去掉")),
])
def test_parse_command(message, expected):
    assert parse_command(message) == expected


def test_parse_command_keeps_the_text_as_written():
    text = "-----BEGIN RSA PRIVATE KEY-----\nabc  def\n  api_key = x"
    assert parse_command(f"/防泄密 test   {text}  ") == ("test", text)


def test_record_counts_once_per_message_and_once_per_rule():
    clock = Clock()
    stats = HitStats(None, clock)
    assert not stats.dirty and stats.total == 0
    stats.record("reply", ["server_path", "internal_url", "server_path"], "logged")
    clock.now += 60
    stats.record("send", ["server_path"], "held")
    stats.record("bogus", ["server_path"], "logged")  # unknown source: ignored
    stats.record("send", ["server_path"], "bogus")  # unknown action: ignored
    assert stats.events == {"reply": 1, "send": 1}
    assert stats.actions == {"logged": 1, "masked": 0, "held": 1}
    assert stats.rules["server_path"] == {"reply": 1, "send": 1, "last": clock.now}
    assert stats.rules["internal_url"] == {"reply": 1, "send": 0, "last": clock.now - 60}
    assert stats.dirty


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "plugin_data" / "hit_stats.json"
    stats = HitStats(path, Clock())
    stats.record("reply", ["jwt"], "masked")
    stats.save()
    assert not stats.dirty and not path.with_name("hit_stats.json.tmp").exists()
    assert b"\r" not in path.read_bytes()
    again = HitStats.load(path, Clock(1_800_000_000.0))
    assert again.to_dict() == stats.to_dict() and not again.dirty and not again.load_error


def test_missing_file_starts_now_and_is_written_at_the_next_save(tmp_path):
    stats = HitStats.load(tmp_path / "hit_stats.json", Clock(123.0))
    assert stats.since == 123.0 and stats.total == 0 and stats.dirty and not stats.load_error


def test_broken_or_odd_files_never_raise(tmp_path):
    path = tmp_path / "hit_stats.json"
    path.write_bytes(b"{not json")
    broken = HitStats.load(path, Clock(5.0))
    assert broken.load_error == "JSONDecodeError" and broken.total == 0 and broken.dirty
    path.write_text(json.dumps({
        "since": "yesterday", "events": {"reply": -3, "send": 2.0, "other": 9}, "actions": {"held": True},
        "rules": {"jwt": {"reply": "x", "send": 4, "last": 99.0}, "": {}, "bad": [1]},
    }), encoding="utf-8")
    odd = HitStats.load(path, Clock(5.0))
    assert odd.since == 5.0 and odd.events == {"reply": 0, "send": 2} and odd.actions["held"] == 0
    assert odd.rules == {"jwt": {"reply": 0, "send": 4, "last": 99.0}}


def test_reset_starts_again():
    clock = Clock()
    stats = HitStats(None, clock)
    stats.record("reply", ["jwt"], "logged")
    clock.now += 3600
    stats.reset()
    assert stats.total == 0 and stats.rules == {} and stats.since == clock.now and stats.dirty
    assert "重新算" in reset_text(stats)


def test_stats_lines_rank_rules_and_never_show_samples():
    clock = Clock()
    stats = HitStats(None, clock)
    assert stats_lines(stats)[0].endswith("还没有命中。")
    stats.record("reply", ["internal_url"], "logged")
    stats.record("send", ["server_path"], "logged")
    stats.record("send", ["server_path", "brand_new_rule"], "held")
    lines = stats_lines(stats)
    assert "回复 1 次、直接发送 2 次；只记日志 2 次、拦下 1 次" in lines[0]
    assert lines[1].startswith("· 服务器路径 server_path：直接发送 2，最近 ")
    assert lines[2].startswith("· brand_new_rule：直接发送 1")  # unknown rules show their name
    assert lines[3].startswith("· 内部地址 internal_url：回复 1")
    assert "outbound hit" in lines[-1]


def test_status_text_shows_counts_not_entries():
    text = status_text("mask", "开，装在 1 个 OneBot 连接上", 8, {"known": 0, "hosts": 5, "paths": 7, "names": 1},
                       HitStats(None, Clock()))
    assert "模式：mask（命中的片段打码后发送）" in text
    assert "AstrBot 配置里的密钥：保护 8 个" in text
    assert "内部主机 5 个、私有路径 7 个、内部名称 1 个" in text
    assert USAGE in text
    assert "不保护" in status_text("log", "关", None, {}, HitStats(None, Clock()))


@pytest.mark.parametrize("mode, spans, left, notice, expected", [
    ("off", 0, False, False, "现在模式是 off，发送时不会检查。"),
    ("log", 1, False, False, "按现在的模式 log：照常发出，日志里记一笔。"),
    ("mask", 2, False, False, "按现在的模式 mask：会把 2 处换成「[已隐藏]」后发出。"),
    ("mask", 2, True, True, "打码后仍有残留，会按 drop 处理：整条不发，改发拦截提示。"),
    ("drop", 1, False, False, "按现在的模式 drop：整条不发，聊天里什么也不发。"),
    ("drop", 1, False, True, "按现在的模式 drop：整条不发，改发拦截提示。"),
])
def test_describe_test_says_what_the_mode_would_do(mode, spans, left, notice, expected):
    text = describe_test(["token_prefix", "server_path"], mode, spans, left, "[已隐藏]", notice)
    assert text.splitlines() == ["测试：命中 2 条规则（不复述原文）", "· 服务令牌 token_prefix",
                                 "· 服务器路径 server_path", expected]


def test_describe_test_without_hits():
    assert describe_test([], "log", 0, False, "x", False) == "测试：没有命中任何规则，这段会照常发出。"
    assert describe_test([], "off", 0, False, "x", False).endswith("（出站检查现在关着。）")


def test_replies_survive_the_formatter():
    # Production formats every outgoing text (llm_only off), the command's replies included.
    stats = HitStats(None, Clock())
    stats.record("send", ["server_path", "token_prefix"], "logged")
    replies = [
        status_text("log", "关", 3, {"known": 1, "hosts": 0, "paths": 0, "names": 0}, stats),
        describe_test(["windows_user_path", "url_credentials"], "mask", 2, False, "[已隐藏]", False),
        USAGE,
    ]
    for reply in replies:
        assert convert(reply) == reply
