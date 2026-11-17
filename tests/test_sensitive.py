"""qq_pretty outbound check rules. privacy-scan: allow-file (every secret below is fake)"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sensitive import (collect_secrets, compile_rules, config_files, entries, find_spans, known_list,
                       load_config_secrets, mask, mode, redact, rules_from_config, scan)

SITE = rules_from_config({
    "internal_hosts": ["my-proxy", "bot-stack-*", "cache.internal"],
    "private_paths": ["/data/my-bot", "/srv/secrets/", "/opt/stack-*"],
    "internal_names": ["bot-stack-*", "ab"],
})


def rules(text, known=(), rule_set=None):
    return [h.rule for h in (scan(text, known) if rule_set is None else scan(text, known, rule_set))]


@pytest.mark.parametrize("text", [
    "token 是多少？", "密码忘了怎么办", "password 记不住就用密码管理器", "这个 api key 要去官网申请",
    "/help 看指令", "路径写 ./data/out.png 就行", "3/4 的人同意", "访问 https://example.com/data/astrbot 看看",
    "哼，本小姐才没有在意你。", "sk-learn 是个库", "./data/astrbot 是相对路径", "/data/astrbotfoo 不是那个目录",
    "文档在 https://astrbot.app/dev/plugin.html", "去 https://docs.napcat.wiki 看", "napcat 是一个 QQ 协议端",
])
def test_ordinary_chat_is_not_a_hit(text):
    assert rules(text) == []
    assert rules(text, rule_set=SITE) == []


@pytest.mark.parametrize("text,rule", [
    ("配置在 /data/astrbot/data/config 里", "server_path"),
    ("看 /AstrBot/data/cmd_config.json", "server_path"),
    ("文件在 plugin_data/astrbot_plugin_example/state.json", "server_path"),
    ("备份在 /data/backups 下", "server_path"),
    ("打开 /home/alice/.ssh 看看", "server_path"),
    (r"在 C:\Users\Alice\workspace 下面", "windows_user_path"),
    ("面板 http://astrbot:6185", "internal_url"),
    ("接口是 http://napcat:3000/send", "internal_url"),
    ("服务在 http://172.18.0.5:8000", "internal_url"),
    ("本机 http://localhost.", "internal_url"),
    ("api_key = abcdefghijklmnop1234", "assigned_secret"),
    ('"token": "Zx9_abcdefghijklmnop"', "assigned_secret"),
    ("sk-proj-abcdefghijklmnopqrstuvwx", "token_prefix"),
    ("ghp_" + "a" * 36, "token_prefix"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmn", "jwt"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private_key"),
])
def test_leaks_are_caught(text, rule):
    assert rule in rules(text)
    assert rule in rules(text, rule_set=SITE)


@pytest.mark.parametrize("text,rule", [
    ("配置在/data/astrbot里", "server_path"),
    ("看/AstrBot/data下面", "server_path"),
    ("地址是http://192.168.1.10:8080", "internal_url"),
    ("密钥sk-proj-abcdefghijklmnopqrstuvwx别外传", "token_prefix"),
    (r"在C:\Users\Alice下面", "windows_user_path"),
])
def test_rules_fire_next_to_chinese_text(text, rule):
    assert rule in rules(text)


@pytest.mark.parametrize("text,rule", [
    ("接口是 http://my-proxy:3425/v1", "internal_url"),
    ("面板 http://BOT-STACK-web:5099", "internal_url"),
    ("缓存 http://cache.internal/stats", "internal_url"),
    ("在 /data/my-bot/files 下面", "server_path"),
    ("在/data/my-bot里", "server_path"),
    ("密钥在 /srv/secrets/app.env", "server_path"),
    ("装在 /opt/stack-blue/bin", "server_path"),
    ("重启容器 bot-stack-astrbot 就好", "internal_name"),
    ("docker logs BOT-STACK-T2I", "internal_name"),
])
def test_site_lists_are_caught(text, rule):
    assert rule in rules(text, rule_set=SITE)
    assert rule not in rules(text)


@pytest.mark.parametrize("text", [
    "my-proxy 是什么", "访问 https://my-proxy.example.com 看看", "/data/my-botx 不是那个目录",
    "访问 https://example.com/data/my-bot 看看", "./data/my-bot 是相对路径", "a b ab abc", "bot-stack 是一种思路",
])
def test_site_lists_stay_in_their_place(text):
    assert rules(text, rule_set=SITE) == []


def test_site_hits_share_the_builtin_rule_names():
    hits = scan("http://my-proxy:1 和 http://napcat:2", rules=SITE)
    assert [h.rule for h in hits] == ["internal_url"]


def test_entries_are_literal_and_clean():
    assert entries([" a.b ", "a.b", "", None, "*", "longer*"]) == ("longer*", "a.b", "*")
    assert entries("one") == ("one",) and entries(None) == ()
    literal = compile_rules(hosts=("a.b",))
    assert "internal_url" in rules("http://a.b:1", rule_set=literal)
    assert rules("http://axb:1", rule_set=literal) == []  # the dot is not a wildcard
    assert compile_rules(names=("*", "ab")) == compile_rules()  # unusable entries add nothing
    assert compile_rules() is compile_rules()  # cached


def test_known_secrets_match_exactly_and_log_only_their_length():
    key = "0123456789abcdef0123456789abcdef"
    hits = scan(f"给你：{key}", known_list([key, "short", "  "]))
    assert [h.rule for h in hits] == ["known_secret"]
    assert hits[0].sample == "…(32)"
    assert known_list("abcdefgh12") == ["abcdefgh12"] and known_list(None) == []


def test_config_secrets_log_their_source_not_their_value():
    key = "z9Y8x7W6v5U4t3S2r1Q0p9O8n7M6l5K4"
    hits = scan(f"是 {key}", config_secrets={key: "cmd_config.json:key"})
    assert [(h.rule, h.sample) for h in hits] == [("config_secret", "cmd_config.json:key(32)")]


@pytest.mark.parametrize("text", [
    "hf_" + "A1b2C3d4" * 4, "glpat-" + "x" * 20, "gho_" + "a" * 36, "AKID" + "Ab12" * 8, "LTAI" + "5tAbCdEfGh12",
    "AKLT" + "Abc_def-1234567890xyz", "ya29." + "a0AfH6SMB" * 3, "sk_live_" + "x1" * 12,
    "rk_test_" + "a" * 24, "xai-" + "Ab1" * 8, "gsk_" + "Ab1" * 8, "npm_" + "a1" * 18,
])
def test_more_token_shapes(text):
    assert "token_prefix" in rules(f"密钥是{text}，别外传")


@pytest.mark.parametrize("text", [
    "123456789:AAH" + "x" * 32, "MTA" + "x" * 21 + ".GxYz12." + "a" * 27,
])
def test_bot_tokens(text):
    assert "bot_token" in rules(f"token {text}")


@pytest.mark.parametrize("text", [
    "redis://default:p4ssw0rd@cache:6379/0", "mysql://root:hunter2@db/app", "https://user:pa55@example.com/x",
])
def test_url_credentials(text):
    assert "url_credentials" in rules(text)


@pytest.mark.parametrize("text", [
    "hf_hub 是个库", "AKID 是缩写", "redis://cache:6379/0", "https://user@example.com", "时间 12:30 开会",
    "比分 3:2", "https://example.com/a:b@c", "LTAI 开头的才是", "ya29 是什么",
])
def test_lookalikes_are_not_tokens(text):
    assert rules(text) == []


def test_redact_hides_values_whole_paths_and_key_blocks():
    rule_set = SITE
    text = ("api_key = abcdefghijklmnop1234；在/data/astrbot/data/cmd_config.json里；"
            "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----\n完；"
            "redis://default:p4ssw0rd@cache:6379；http://my-proxy:8080/v1")
    out = redact(text, rules=rule_set, placeholder="##")
    assert out == "api_key = ##；在##里；##\n完；redis://##@cache:6379；##/v1"


def test_redact_merges_overlaps_and_every_occurrence():
    key = "0123456789abcdef0123"
    assert redact(f"{key} 和 {key}", [key]) == "[已隐藏] 和 [已隐藏]"
    # The known secret sits inside a token match: one span, one placeholder.
    token = "sk-proj-" + key + "xyz"
    assert redact(f"是{token}。", [key]) == "是[已隐藏]。"
    assert find_spans("没有东西") == []
    assert redact("-----BEGIN RSA PRIVATE KEY-----\nMIIE") == "[已隐藏]"


def test_collect_secrets_by_key_name():
    data = {
        "provider": [{"key": ["Ab1" * 6, ""], "model": "gpt-5-mini-2026", "api_base": "https://api.example.com"}],
        "platform": [{"ws_reverse_token": "tok-12345678", "secret": "short", "appid": "1234567890"}],
        "dashboard": {"password": "5f4dcc3b5aa765d61d8327deb882cf99", "jwt-secret": "jwt value with spaces"},
        "replies": [{"key": "早上好早上好早上好", "reply": "hi"}, {"keys": ["goodmorning_everyone"]}],
        "nested": {"cfg": {"T2I_ACCESS_TOKEN": "t2i-token-abcdef"}}, "max_tokens": 4096,
    }
    assert collect_secrets(data) == {
        "Ab1" * 6: "key", "tok-12345678": "ws_reverse_token",
        "5f4dcc3b5aa765d61d8327deb882cf99": "password", "t2i-token-abcdef": "T2I_ACCESS_TOKEN",
    }


def test_load_config_secrets_reads_every_file_that_parses(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "cmd_config.json").write_text('{"api_key": "main-key-0001"}', encoding="utf-8-sig")
    (tmp_path / "config" / "a_config.json").write_text('{"token": "plugin-token-01"}', encoding="utf-8")
    (tmp_path / "config" / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "config" / "notes.txt").write_text('{"token": "ignored-not-json-file"}', encoding="utf-8")
    secrets, parsed = load_config_secrets(config_files(tmp_path))
    assert secrets == {"plugin-token-01": "a_config.json:token", "main-key-0001": "cmd_config.json:api_key"}
    assert parsed == 2
    assert list(secrets) == sorted(secrets, key=lambda s: (-len(s), s))
    assert load_config_secrets(config_files(tmp_path / "missing")) == ({}, 0)


def test_samples_never_carry_the_whole_match():
    hits = scan("api_key=abcdefghijklmnop1234")
    assert hits[0].sample.startswith("api_") and "abcdefghijklmnop1234" not in hits[0].sample
    assert mask("abc") == "***"


def test_modes():
    assert mode("DROP") == "drop" and mode("off") == "off" and mode(" Mask ") == "mask"
    assert mode(None) == "log" and mode("block") == "log"
