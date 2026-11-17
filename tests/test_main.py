"""check_outbound end to end on stub events. privacy-scan: allow-file (every secret below is fake)"""
from __future__ import annotations

import asyncio
import json
import logging
import types

import pytest

from astrbot.core.message.components import Image, Plain
from astrbot_plugin_qq_pretty import main

FAKE_KEY = "sk-" + "abcdefghijklmnopqrstuvwx1234"
CONFIG_KEY = "z9Y8x7W6v5U4t3S2r1Q0p9O8n7M6l5K4"  # bare key, no rule can see it


class Result:
    def __init__(self, chain, llm=True):
        self.chain = chain
        self._llm = llm

    def is_llm_result(self):
        return self._llm


class Event:
    def __init__(self, chain, platform="aiocqhttp", llm=True):
        self._result = Result(chain, llm)
        self._platform = platform

    def get_platform_name(self):
        return self._platform

    def get_result(self):
        return self._result


def plugin(outbound, **cfg):
    return main.QQPrettyPlugin(None, {"outbound_check": outbound, **cfg})


@pytest.fixture(autouse=True)
def stats_dir(tmp_path, monkeypatch):
    """Hit counts go to a throwaway plugin_data, never next to the repo."""
    path = tmp_path / "plugin_data" / main.PLUGIN_NAME
    monkeypatch.setattr(main, "_plugin_data_dir", lambda: path)
    return path


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "cmd_config.json").write_text(json.dumps({
        "provider": [{"id": "p1", "key": [CONFIG_KEY], "model": "some-model-name-1234"}],
        "dashboard": {"username": "admin", "jwt_secret": "jwt-secret-value-0001"},
    }), encoding="utf-8-sig")
    monkeypatch.setattr(main, "_astrbot_data_dir", lambda: tmp_path)
    return tmp_path


def run(p, ev):
    asyncio.run(p.check_outbound(ev))
    return ev.get_result().chain


def texts(chain):
    return [c.text for c in chain if isinstance(c, Plain)]


def test_log_mode_sends_everything(data_dir, caplog):
    caplog.set_level(logging.WARNING)
    chain = run(plugin({"mode": "log"}), Event([Plain(f"key {FAKE_KEY}")]))
    assert texts(chain) == [f"key {FAKE_KEY}"]
    assert "rules=token_prefix" in caplog.text and FAKE_KEY not in caplog.text


def test_mask_mode_hides_only_the_hit(data_dir):
    chain = run(plugin({"mode": "mask"}), Event([Plain(f"先看 {FAKE_KEY} 再说"), Image("x")]))
    assert texts(chain) == ["先看 [已隐藏] 再说"]
    assert isinstance(chain[1], Image)


def test_mask_mode_uses_the_configured_text_and_keeps_names(data_dir):
    chain = run(plugin({"mode": "mask", "mask_text": "***"}),
                Event([Plain("api_key = abcdefghijklmnop1234，配置在/data/astrbot/data/cmd_config.json里")]))
    assert texts(chain) == ["api_key = ***，配置在***里"]


def test_mask_split_across_segments_holds_the_reply(data_dir, caplog):
    # Text segments of one message arrive joined, so a key cut in two is still a leak; it cannot be masked
    # piece by piece, so the reply is held.
    caplog.set_level(logging.WARNING)
    half = len(FAKE_KEY) // 2
    chain = run(plugin({"mode": "mask", "drop_notice": "这条没有发出"}),
                Event([Plain("给你 " + FAKE_KEY[:half]), Plain(FAKE_KEY[half:] + " 收好")]))
    assert texts(chain) == ["这条没有发出"]
    assert "mask left rules=token_prefix" in caplog.text


def test_mask_handles_each_segment(data_dir):
    chain = run(plugin({"mode": "mask"}), Event([Plain("一是 " + FAKE_KEY + "。"), Plain("二是 http://napcat:3000 。")]))
    assert texts(chain) == ["一是 [已隐藏]。", "二是 [已隐藏] 。"]


def test_drop_mode_clears_or_sends_the_notice(data_dir):
    assert run(plugin({"mode": "drop"}), Event([Plain(FAKE_KEY)])) == []
    chain = run(plugin({"mode": "drop", "drop_notice": " 这条回复含敏感信息，没有发出 "}), Event([Plain(FAKE_KEY)]))
    assert texts(chain) == ["这条回复含敏感信息，没有发出"]


def test_config_secrets_are_caught_and_logged_by_source(data_dir, caplog):
    caplog.set_level(logging.INFO)
    p = plugin({"mode": "mask"})
    asyncio.run(p.initialize())
    assert "protecting 2 config secrets from 1 config files" in caplog.text
    chain = run(p, Event([Plain(f"密钥是 {CONFIG_KEY}")]))
    assert texts(chain) == ["密钥是 [已隐藏]"]
    assert "config_secret" in caplog.text and "cmd_config.json:key(32)" in caplog.text
    assert CONFIG_KEY not in caplog.text and CONFIG_KEY[:4] not in caplog.text


def test_config_secrets_can_be_switched_off(data_dir):
    chain = run(plugin({"mode": "mask", "protect_config_secrets": False}), Event([Plain(CONFIG_KEY)]))
    assert texts(chain) == [CONFIG_KEY]


def test_config_secrets_are_read_again_after_the_refresh(data_dir, monkeypatch):
    p = plugin({"mode": "mask"})
    asyncio.run(p.initialize())
    new_key = "Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6"
    (data_dir / "config" / "some_plugin_config.json").write_text(json.dumps({"api_key": new_key}), encoding="utf-8")
    assert texts(run(p, Event([Plain(new_key)]))) == [new_key]  # not read yet
    monkeypatch.setattr(main, "SECRETS_REFRESH_SEC", 0)
    assert texts(run(p, Event([Plain(new_key)]))) == ["[已隐藏]"]


def test_unreadable_config_dir_does_not_break_the_check(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_astrbot_data_dir", lambda: tmp_path / "missing")
    chain = run(plugin({"mode": "mask"}), Event([Plain(FAKE_KEY)]))
    assert texts(chain) == ["[已隐藏]"]


def test_off_mode_and_other_platforms_are_left_alone(data_dir):
    assert texts(run(plugin({"mode": "off"}), Event([Plain(FAKE_KEY)]))) == [FAKE_KEY]
    assert texts(run(plugin({"mode": "drop"}), Event([Plain(FAKE_KEY)], platform="webchat"))) == [FAKE_KEY]


# ---- send gate ---------------------------------------------------------------------------------


class Api:
    """Stands in for aiocqhttp's UnifiedApi: call_action is a class method, the gate goes on the instance."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        if self.fail:
            raise self.fail
        return {"message_id": len(self.calls)}


class Bot:
    def __init__(self, api):
        self.api = api

    async def call_action(self, action, **params):  # CQHttp.call_action passes action by keyword
        return await self.api.call_action(action=action, **params)


class Platform:
    def __init__(self, name="aiocqhttp", api=None):
        self.name = name
        self.bot = Bot(api or Api())

    def meta(self):
        return types.SimpleNamespace(name=self.name)

    def get_client(self):
        return self.bot


def gated(outbound, *platforms, **cfg):
    context = types.SimpleNamespace(platform_manager=types.SimpleNamespace(platform_insts=list(platforms)))
    return main.QQPrettyPlugin(context, {"outbound_check": outbound, **cfg})


def text_msg(text):
    return [{"type": "text", "data": {"text": text}}]


def sent_text(api, i=-1):
    action, params = api.calls[i]
    return action, "".join(seg["data"]["text"] for seg in params["message"] if seg["type"] == "text")


def test_gate_goes_on_onebot_clients_only_and_comes_off_at_terminate(data_dir, caplog):
    caplog.set_level(logging.INFO)
    qq, official = Platform(), Platform("qq_official")
    p = gated({"mode": "log"}, qq, official)
    asyncio.run(p.initialize())
    assert "call_action" in vars(qq.bot.api) and "call_action" not in vars(official.bot.api)
    assert "send gate installed on 1 OneBot client(s) (initialize)" in caplog.text
    asyncio.run(p.terminate())
    assert "call_action" not in vars(qq.bot.api)


def test_gate_can_be_switched_off(data_dir):
    qq = Platform()
    asyncio.run(gated({"mode": "mask", "check_direct_sends": False}, qq).initialize())
    asyncio.run(gated({"mode": "mask"}, qq, platforms=["webchat"]).initialize())
    assert "call_action" not in vars(qq.bot.api)


def test_platform_loaded_later_gets_the_gate(data_dir, caplog):
    caplog.set_level(logging.INFO)
    p = gated({"mode": "mask"})
    asyncio.run(p.initialize())
    assert "send gate waiting, no OneBot client yet" in caplog.text
    qq = Platform()
    p.context.platform_manager.platform_insts.append(qq)
    asyncio.run(p.on_platform_loaded())
    asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg(f"是 {FAKE_KEY}")))
    assert sent_text(qq.bot.api) == ("send_group_msg", "是 [已隐藏]")


def test_terminate_takes_the_gate_off_a_reloaded_platforms_old_client_too(data_dir):
    old = Platform()
    p = gated({"mode": "mask"}, old)
    asyncio.run(p.initialize())
    new = Platform()
    p.context.platform_manager.platform_insts[:] = [new]  # the platform reload swaps in a new client
    asyncio.run(p.on_platform_loaded())
    assert "call_action" in vars(new.bot.api)
    asyncio.run(p.terminate())
    assert "call_action" not in vars(old.bot.api) and "call_action" not in vars(new.bot.api)


def test_mask_rewrites_direct_sends_both_ways_of_calling(data_dir):
    qq = Platform()
    p = gated({"mode": "mask"}, qq)
    asyncio.run(p.initialize())
    image = {"type": "image", "data": {"file": "base64://AAAA"}}
    message = [*text_msg(f"key {FAKE_KEY}"), image]
    result = asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=7, self_id=9, message=message))
    assert result == {"message_id": 1}
    action, params = qq.bot.api.calls[-1]
    assert params["message"][0]["data"]["text"] == "key [已隐藏]" and params["message"][1] is image
    assert params["group_id"] == 7 and params["self_id"] == 9
    assert message[0]["data"]["text"] == f"key {FAKE_KEY}"  # the caller's list is untouched
    asyncio.run(qq.bot.call_action("send_private_msg", user_id=3, message=text_msg(FAKE_KEY)))
    assert sent_text(qq.bot.api) == ("send_private_msg", "[已隐藏]")


def test_mask_covers_forward_nodes_and_file_names(data_dir):
    qq = Platform()
    asyncio.run(gated({"mode": "mask"}, qq).initialize())
    node = {"type": "node", "data": {"nickname": "http://napcat:3000", "user_id": "1",
                                      "content": text_msg(f"密钥 {FAKE_KEY}")}}
    asyncio.run(qq.bot.api.call_action("send_group_forward_msg", group_id=1, messages=[node]))
    data = qq.bot.api.calls[-1][1]["messages"][0]["data"]
    assert data["nickname"] == "[已隐藏]" and data["content"][0]["data"]["text"] == "密钥 [已隐藏]"
    asyncio.run(qq.bot.api.call_action("upload_group_file", group_id=1, file="/AstrBot/data/temp/x.txt",
                                       name=f"{FAKE_KEY}.txt"))
    assert qq.bot.api.calls[-1][1] == {"group_id": 1, "file": "/AstrBot/data/temp/x.txt", "name": "[已隐藏].txt"}


def test_drop_sends_the_notice_for_messages_and_nothing_for_forwards(data_dir):
    qq = Platform()
    asyncio.run(gated({"mode": "drop", "drop_notice": "这条没有发出"}, qq).initialize())
    asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, self_id=2, message=text_msg(FAKE_KEY)))
    assert qq.bot.api.calls[-1] == ("send_group_msg", {"group_id": 1, "self_id": 2, "message": text_msg("这条没有发出")})
    node = {"type": "node", "data": {"nickname": "a", "content": text_msg(FAKE_KEY)}}
    assert asyncio.run(qq.bot.api.call_action("send_group_forward_msg", group_id=1, messages=[node])) is None
    assert len(qq.bot.api.calls) == 1


def test_drop_without_notice_returns_none_and_sends_nothing(data_dir):
    qq = Platform()
    asyncio.run(gated({"mode": "drop"}, qq).initialize())
    assert asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg(FAKE_KEY))) is None
    assert qq.bot.api.calls == []


def test_log_mode_names_the_calling_plugin_and_sends_as_is(data_dir, caplog):
    caplog.set_level(logging.WARNING)
    qq = Platform()
    asyncio.run(gated({"mode": "log"}, qq).initialize())
    weather = types.ModuleType("data.plugins.astrbot_plugin_weather.main")
    exec("async def say(api, message):\n    return await api.call_action('send_group_msg', group_id=1, message=message)",
         weather.__dict__)
    asyncio.run(weather.say(qq.bot.api, text_msg(f"key {FAKE_KEY}")))
    assert sent_text(qq.bot.api) == ("send_group_msg", f"key {FAKE_KEY}")
    assert "send-layer hit action=send_group_msg caller=astrbot_plugin_weather mode=log rules=token_prefix" in caplog.text
    assert FAKE_KEY not in caplog.text


def test_config_secrets_reach_the_gate(data_dir, caplog):
    caplog.set_level(logging.WARNING)
    qq = Platform()
    asyncio.run(gated({"mode": "mask"}, qq).initialize())
    asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg(f"是{CONFIG_KEY}")))
    assert sent_text(qq.bot.api) == ("send_group_msg", "是[已隐藏]")
    assert "cmd_config.json:key(32)" in caplog.text and CONFIG_KEY not in caplog.text


def test_reply_already_logged_is_not_logged_again_at_the_gate(data_dir, caplog):
    caplog.set_level(logging.WARNING)
    qq = Platform()
    p = gated({"mode": "log"}, qq)

    async def one_reply():
        await p.initialize()
        event = Event([Plain(f"key {FAKE_KEY}")])
        await p.check_outbound(event)  # the pipeline then sends the reply in the same task
        await qq.bot.call_action("send_group_msg", group_id=1, message=text_msg(event.get_result().chain[0].text))
        await qq.bot.call_action("send_group_msg", group_id=1, message=text_msg("http://napcat:3000"))

    asyncio.run(one_reply())
    assert caplog.text.count("outbound hit") == 1
    assert caplog.text.count("send-layer hit") == 1 and "rules=internal_url" in caplog.text


def test_a_task_spawned_after_the_reply_still_logs_its_own_send(data_dir, caplog):
    # A background task (a follow-up message) inherits the reply's context; same hit, different text: logged.
    caplog.set_level(logging.WARNING)
    qq = Platform()
    p = gated({"mode": "log"}, qq)

    async def reply_then_supplement():
        await p.initialize()
        await p.check_outbound(Event([Plain(f"key {FAKE_KEY}")]))
        await asyncio.create_task(qq.bot.api.call_action("send_group_msg", group_id=1,
                                                         message=text_msg(f"补一句：{FAKE_KEY}")))

    asyncio.run(reply_then_supplement())
    assert caplog.text.count("outbound hit") == 1 and caplog.text.count("send-layer hit") == 1


def test_mask_and_drop_always_log_at_the_gate(data_dir, caplog):
    caplog.set_level(logging.WARNING)
    qq = Platform()
    p = gated({"mode": "mask"}, qq)

    async def flow():
        await p.initialize()
        await p.check_outbound(Event([Plain("先说"), Plain(FAKE_KEY[:5]), Plain(FAKE_KEY[5:])]))  # held
        await qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg(FAKE_KEY))

    asyncio.run(flow())
    assert caplog.text.count("send-layer hit") == 1


def test_clean_messages_and_other_actions_pass_untouched(data_dir):
    qq = Platform()
    asyncio.run(gated({"mode": "drop"}, qq).initialize())
    message = text_msg("早上好")
    asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, message=message))
    asyncio.run(qq.bot.api.call_action("get_group_info", group_id=FAKE_KEY))
    assert qq.bot.api.calls[0][1]["message"] is message
    assert qq.bot.api.calls[1] == ("get_group_info", {"group_id": FAKE_KEY})


def test_a_gate_bug_sends_as_is_and_send_errors_pass_through(data_dir, caplog, monkeypatch):
    qq = Platform(api=Api(fail=RuntimeError("muted")))
    asyncio.run(gated({"mode": "drop"}, qq).initialize())
    with pytest.raises(RuntimeError, match="muted"):
        asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg("hi")))
    assert len(qq.bot.api.calls) == 1  # the original runs exactly once

    def boom(*a, **k):
        raise ValueError("bug")

    monkeypatch.setattr(main.send_gate, "texts", boom)
    qq.bot.api.fail = None
    asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg(FAKE_KEY)))
    assert sent_text(qq.bot.api) == ("send_group_msg", FAKE_KEY)
    assert "send gate check failed, sending as is" in caplog.text


def test_after_terminate_a_gate_that_could_not_come_off_passes_through(data_dir):
    qq = Platform()
    p = gated({"mode": "drop"}, qq)
    asyncio.run(p.initialize())
    ours = qq.bot.api.call_action

    async def on_top(*args, **params):
        return await ours(*args, **params)

    qq.bot.api.call_action = on_top
    asyncio.run(p.terminate())
    asyncio.run(qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg(FAKE_KEY)))
    assert sent_text(qq.bot.api) == ("send_group_msg", FAKE_KEY)


# ---- hit counts and the admin command ----------------------------------------------------------


class CommandEvent:
    def __init__(self, message):
        self.message = message

    def get_message_str(self):
        return self.message

    def plain_result(self, text):
        return text


def command(p, message):
    async def first_reply():
        async for reply in p.outbound_guard(CommandEvent(message)):
            return reply

    return asyncio.run(first_reply())


def saved_stats(stats_dir):
    return json.loads((stats_dir / main.STATS_FILE).read_text(encoding="utf-8"))


def test_command_is_admin_only_and_silent_for_members():
    assert main.QQPrettyPlugin.outbound_guard._test_command == ("outbound_guard", {"防泄密"})
    assert main.QQPrettyPlugin.outbound_guard._test_permission == ("admin", False)


def test_hits_are_counted_once_per_reply_and_saved(data_dir, stats_dir):
    qq = Platform()
    p = gated({"mode": "log"}, qq)

    async def flow():
        await p.initialize()
        event = Event([Plain(f"key {FAKE_KEY}")])
        await p.check_outbound(event)
        # The pipeline sends the reply the check just logged: not counted again.
        await qq.bot.call_action("send_group_msg", group_id=1, message=text_msg(event.get_result().chain[0].text))
        await qq.bot.call_action("send_group_msg", group_id=1, message=text_msg("http://napcat:3000"))
        await p.check_outbound(Event([Plain("早上好")]))

    asyncio.run(flow())
    assert saved_stats(stats_dir)["events"] == {"reply": 0, "send": 0}  # written at initialize, not yet since
    asyncio.run(p.terminate())
    saved = saved_stats(stats_dir)
    assert saved["events"] == {"reply": 1, "send": 1}
    assert saved["actions"] == {"logged": 2, "masked": 0, "held": 0}
    assert set(saved["rules"]) == {"token_prefix", "internal_url"}
    assert FAKE_KEY not in (stats_dir / main.STATS_FILE).read_text(encoding="utf-8")


def test_masked_and_held_hits_are_counted_by_action(data_dir, stats_dir):
    qq = Platform()
    p = gated({"mode": "mask"}, qq)

    async def flow():
        await p.initialize()
        await p.check_outbound(Event([Plain(f"key {FAKE_KEY}")]))  # masked
        await p.check_outbound(Event([Plain("先说"), Plain(FAKE_KEY[:5]), Plain(FAKE_KEY[5:])]))  # held
        await qq.bot.api.call_action("send_group_msg", group_id=1, message=text_msg(FAKE_KEY))  # masked

    asyncio.run(flow())
    stats = p._hit_stats()
    assert stats.events == {"reply": 2, "send": 1}
    assert stats.actions == {"logged": 0, "masked": 2, "held": 1}
    p2 = gated({"mode": "drop"}, qq)
    asyncio.run(p2.check_outbound(Event([Plain(FAKE_KEY)])))
    assert p2._hit_stats().actions["held"] == 1


def test_counts_survive_a_reload(data_dir, stats_dir):
    first = plugin({"mode": "log"})
    asyncio.run(first.initialize())
    run(first, Event([Plain(FAKE_KEY)]))
    asyncio.run(first.terminate())
    second = plugin({"mode": "log"})
    asyncio.run(second.initialize())
    assert second._hit_stats().events["reply"] == 1
    assert second._hit_stats().since == first._hit_stats().since


def test_off_mode_and_clean_text_are_not_counted(data_dir):
    p = plugin({"mode": "off"})
    run(p, Event([Plain(FAKE_KEY)]))
    run(plugin({"mode": "log"}), Event([Plain("早上好")]))
    assert p._hit_stats().total == 0


def test_a_broken_stats_file_is_replaced_and_counting_goes_on(data_dir, stats_dir, caplog):
    caplog.set_level(logging.WARNING)
    stats_dir.mkdir(parents=True)
    (stats_dir / main.STATS_FILE).write_text("{oops", encoding="utf-8")
    p = plugin({"mode": "log"})
    asyncio.run(p.initialize())
    assert "hit stats unreadable (JSONDecodeError), counting from now" in caplog.text
    run(p, Event([Plain(FAKE_KEY)]))
    asyncio.run(p.terminate())
    assert saved_stats(stats_dir)["events"]["reply"] == 1


def test_counting_failure_never_breaks_the_check(data_dir, monkeypatch, caplog):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(main.selfcheck.HitStats, "record", boom)
    chain = run(plugin({"mode": "mask"}), Event([Plain(FAKE_KEY)]))
    assert texts(chain) == ["[已隐藏]"]
    assert "counting a hit failed" in caplog.text


def test_status_shows_state_and_counts_without_values(data_dir, stats_dir):
    qq = Platform()
    p = gated({"mode": "mask", "known_secrets": ["a-known-secret-1"], "internal_hosts": ["my-proxy", "bot-*"],
               "private_paths": ["/data/my-bot"], "internal_names": []}, qq)
    asyncio.run(p.initialize())
    run(p, Event([Plain(FAKE_KEY)]))
    reply = command(p, "/防泄密")
    assert "模式：mask（命中的片段打码后发送）" in reply
    assert "也查直接发送：开，装在 1 个 OneBot 连接上" in reply
    assert "AstrBot 配置里的密钥：保护 2 个" in reply
    assert "已知密钥 1 个；内部主机 2 个、私有路径 1 个、内部名称 0 个" in reply
    assert "回复 1 次；打码 1 次" in reply and "· 服务令牌 token_prefix：回复 1" in reply
    for value in (FAKE_KEY, CONFIG_KEY, "a-known-secret-1", "my-proxy", "/data/my-bot"):
        assert value not in reply
    assert saved_stats(stats_dir)["events"]["reply"] == 1  # the status flushes what it shows


def test_status_when_the_gate_or_protection_is_off(data_dir):
    reply = command(gated({"mode": "log", "check_direct_sends": False, "protect_config_secrets": False}),
                    "防泄密 状态")
    assert "也查直接发送：关" in reply and "AstrBot 配置里的密钥：不保护" in reply
    reply = command(gated({"mode": "log"}), "防泄密")
    assert "也查直接发送：开，但还没装上（没有 OneBot 连接）" in reply


def test_self_test_names_rules_never_the_text_and_is_not_counted(data_dir, caplog):
    caplog.set_level(logging.INFO)
    p = plugin({"mode": "mask", "internal_names": ["mybot-prod-*"]})
    asyncio.run(p.initialize())
    secret_text = f"密钥 {FAKE_KEY}，配置 {CONFIG_KEY}，容器 mybot-prod-astrbot"
    reply = command(p, f"/防泄密 测试 {secret_text}")
    assert reply.splitlines() == [
        "测试：命中 3 条规则（不复述原文）",
        "· AstrBot 配置里的密钥 config_secret",
        "· 服务令牌 token_prefix",
        "· 内部名称 internal_name",
        "按现在的模式 mask：会把 3 处换成「[已隐藏]」后发出。",
    ]
    for value in (FAKE_KEY, CONFIG_KEY, "mybot-prod-astrbot", FAKE_KEY[:4]):
        assert value not in reply and value not in caplog.text
    assert "self-test mode=mask rules=config_secret,token_prefix,internal_name chars=" in caplog.text
    assert p._hit_stats().total == 0


def test_self_test_follows_the_mode_and_notice(data_dir):
    assert command(plugin({"mode": "drop", "drop_notice": "没发出"}), f"防泄密 test {FAKE_KEY}").endswith(
        "按现在的模式 drop：整条不发，改发拦截提示。")
    assert command(plugin({"mode": "log"}), "防泄密 测试 早上好") == "测试：没有命中任何规则，这段会照常发出。"
    assert command(plugin({"mode": "log"}), "防泄密 测试").startswith("用法：/防泄密 测试 <文本>")


def test_self_test_reply_passes_the_outbound_check(data_dir):
    p = plugin({"mode": "drop"})
    reply = command(p, f"防泄密 测试 {FAKE_KEY} http://napcat:3000 /data/astrbot/x")
    assert texts(run(p, Event([Plain(reply)], llm=False))) == [reply]
    assert p._hit_stats().total == 0


def test_reset_and_unknown_words(data_dir, stats_dir):
    p = plugin({"mode": "log"})
    run(p, Event([Plain(FAKE_KEY)]))
    assert p._hit_stats().total == 1
    assert command(p, "/outbound_guard reset").startswith("命中统计已清零，从 ")
    assert p._hit_stats().total == 0 and saved_stats(stats_dir)["events"]["reply"] == 0
    assert command(p, "防泄密 帮我看看") == main.selfcheck.USAGE


def test_a_command_bug_answers_without_details(data_dir, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(main.sensitive, "scan", boom)
    assert command(plugin({"mode": "log"}), f"防泄密 测试 {FAKE_KEY}") == "自检出错了，详情看日志。"
    assert "outbound_guard command failed" in caplog.text
