"""Send gate payload reading, rewriting and install. privacy-scan: allow-file (every secret below is fake)"""
from __future__ import annotations

import asyncio
import copy
import types

import pytest

from gate import (FIELD_ACTIONS, FORWARD_ACTIONS, MESSAGE_ACTIONS, checked, install, notice_params, owner_of,
                  plugin_of, remove, rewrite, texts)

SECRET = "sk-" + "abcdefghijklmnopqrstuvwx1234"
B64 = "base64://" + "c2stYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4MTIzNA" * 50


def up(text: str) -> str:
    return text.upper()


class Segment(dict):
    """aiocqhttp's MessageSegment is a dict subclass."""


class Message(list):
    """aiocqhttp's Message is a list subclass of segments."""


def test_which_actions_are_checked():
    for action in (*MESSAGE_ACTIONS, *FORWARD_ACTIONS, *FIELD_ACTIONS):
        assert checked(action)
    for action in ("get_group_info", "set_msg_emoji_like", "delete_msg", None, 3):
        assert not checked(action)


def test_message_text_segments_are_one_unit_and_media_is_never_read():
    message = [{"type": "text", "data": {"text": "先看 "}}, {"type": "image", "data": {"file": B64}},
               {"type": "at", "data": {"qq": "123"}}, {"type": "text", "data": {"text": "再说"}},
               {"type": "record", "data": {"file": "/AstrBot/data/temp/a.amr"}}]
    assert texts("send_group_msg", {"group_id": 1, "message": message}) == ["先看 再说"]


def test_aiocqhttp_message_objects_and_single_segments():
    message = Message([Segment(type="text", data={"text": "a"}), Segment(type="face", data={"id": "1"})])
    assert texts("send_msg", {"message": message}) == ["a"]
    assert texts("send_msg", {"message": {"type": "text", "data": {"text": "solo"}}}) == ["solo"]


def test_cq_strings_drop_codes_and_unescape():
    message = "看&#91;这个&#93; [CQ:image,file=/AstrBot/data/x.png] A&amp;B"
    assert texts("send_private_msg", {"message": message}) == ["看[这个]  A&B"]
    assert texts("send_private_msg", {"message": "[CQ:at,qq=1]", "auto_escape": True}) == ["[CQ:at,qq=1]"]


def test_file_and_markdown_segments():
    message = [{"type": "file", "data": {"name": "cmd_config.json", "file": "file:///AstrBot/data/cmd_config.json"}},
               {"type": "markdown", "data": {"content": "**md**"}}]
    assert texts("send_group_msg", {"message": message}) == ["**md**", "cmd_config.json"]


def test_forward_nodes_names_contents_and_card_text():
    nodes = [
        {"type": "node", "data": {"name": "甲", "uin": "1", "content": [{"type": "text", "data": {"text": "一"}}]}},
        {"type": "node", "data": {"nickname": "乙", "user_id": "2", "content": "二[CQ:face,id=1]"}},
        {"type": "node", "data": {"id": "123456"}},  # forwarding an existing message: nothing to read
        {"type": "node", "data": {"nickname": "丙", "content": [
            {"messages": [{"type": "node", "data": {"nickname": "丁", "content": [
                {"type": "text", "data": {"text": "套娃"}}, {"type": "image", "data": {"file": B64}}]}}]}]}},
    ]
    params = {"group_id": 1, "messages": nodes, "news": [{"text": "新闻"}], "prompt": "提示", "summary": "摘要"}
    assert texts("send_group_forward_msg", params) == ["甲", "一", "乙", "二", "丙", "丁", "套娃", "新闻", "提示", "摘要"]


def test_field_actions():
    assert texts("upload_group_file", {"file": "/AstrBot/data/x.png", "name": "x.png"}) == ["x.png"]
    assert texts("_send_group_notice", {"content": "公告"}) == ["公告"]
    assert texts("send_group_ai_record", {"text": "念出来", "character": "c"}) == ["念出来"]


def test_rewrite_copies_only_text_and_never_touches_the_callers_objects():
    image = {"type": "image", "data": {"file": B64}}
    message = [{"type": "text", "data": {"text": "a"}}, image, {"type": "reply", "data": {"id": "9"}}]
    params = {"group_id": 1, "self_id": 2, "message": message}
    before = copy.deepcopy(params)
    out = rewrite("send_group_msg", params, up)
    assert params == before
    assert out["message"][0] == {"type": "text", "data": {"text": "A"}}
    assert out["message"][1] is image and out["group_id"] == 1 and out["self_id"] == 2


def test_rewrite_cq_strings_keeps_codes_and_escapes_new_text():
    out = rewrite("send_msg", {"message": "a&#91;b [CQ:at,qq=1] c"}, lambda s: s.replace("b", "[x]"))
    assert out["message"] == "a&#91;&#91;x&#93; [CQ:at,qq=1] c"
    untouched = "a&#91;b [CQ:at,qq=1]"
    assert rewrite("send_msg", {"message": untouched}, lambda s: s)["message"] == untouched
    assert rewrite("send_msg", {"message": "[CQ:x]", "auto_escape": True}, up)["message"] == "[CQ:X]"


def test_rewrite_forward_nodes_and_fields():
    nodes = [{"type": "node", "data": {"name": "a", "uin": "1", "content": [
        {"type": "text", "data": {"text": "b"}}, {"type": "image", "data": {"file": B64}}]}}]
    params = {"messages": nodes, "news": [{"text": "c"}], "summary": "d"}
    before = copy.deepcopy(params)
    out = rewrite("send_private_forward_msg", params, up)
    assert params == before
    node = out["messages"][0]["data"]
    assert node["name"] == "A" and node["uin"] == "1" and node["content"][0]["data"]["text"] == "B"
    assert node["content"][1]["data"]["file"] == B64
    assert out["news"] == [{"text": "C"}] and out["summary"] == "D"
    assert rewrite("upload_group_file", {"name": "n", "file": "/x/n"}, up) == {"name": "N", "file": "/x/n"}


def test_notice_replaces_the_message_and_keeps_the_target():
    params = {"group_id": 1, "self_id": 2, "message": "x", "auto_escape": True}
    assert notice_params("send_group_msg", params, "没发出") == {
        "group_id": 1, "self_id": 2, "message": [{"type": "text", "data": {"text": "没发出"}}]}
    assert notice_params("send_group_forward_msg", {"group_id": 1}, "没发出") is None
    assert notice_params("upload_group_file", {"group_id": 1}, "没发出") is None
    assert notice_params("send_group_msg", params, "") is None


def frames(*names):
    frame = None
    for name in reversed(names):
        frame = types.SimpleNamespace(f_globals={"__name__": name}, f_back=frame)
    return frame


def test_plugin_of_names_the_calling_plugin():
    own = "astrbot_plugin_qq_pretty"
    assert plugin_of(frames("data.plugins.astrbot_plugin_qq_pretty.main", "aiocqhttp",
                            "data.plugins.astrbot_plugin_weather.main", "astrbot.core.pipeline"), own) == "astrbot_plugin_weather"
    assert plugin_of(frames("astrbot_plugin_qq_pretty.main", "aiocqhttp",
                            "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event",
                            "astrbot.core.pipeline.respond.stage"), own) == "astrbot"
    assert plugin_of(frames("astrbot.builtin_stars.builtin_commands.main"), own) == "builtin_commands"
    assert plugin_of(frames("data.plugins.meme_maker.core"), own) == "meme_maker"
    assert plugin_of(frames("asyncio.tasks"), own) == "unknown"


class Api:
    def __init__(self):
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))
        return {"message_id": len(self.calls)}


def wrapping(tag, log):
    def make(original):
        async def gate(*args, **params):
            log.append(tag)
            return await original(*args, **params)

        return gate

    return make


def test_install_and_remove_put_back_the_class_method():
    api, owner, log = Api(), object(), []
    assert install(api, owner, wrapping("g", log))
    assert not install(api, owner, wrapping("again", log))  # already there
    assert owner_of(api) is owner
    asyncio.run(api.call_action("send_msg", message="x"))
    asyncio.run(api.call_action(action="send_msg", message="y"))
    assert log == ["g", "g"] and api.calls == [("send_msg", {"message": "x"}), ("send_msg", {"message": "y"})]
    assert remove(api, owner)
    assert "call_action" not in vars(api) and owner_of(api) is None


def test_a_stale_gate_is_replaced_not_wrapped_again():
    api, log = Api(), []
    install(api, object(), wrapping("old", log))
    new_owner = object()
    assert install(api, new_owner, wrapping("new", log))
    asyncio.run(api.call_action("send_msg"))
    assert log == ["new"]
    assert remove(api, new_owner) and "call_action" not in vars(api)


def test_someone_elses_wrapper_is_kept_and_not_removed():
    api, owner, log = Api(), object(), []

    async def theirs(*args, **params):
        log.append("theirs")
        return await Api.call_action(api, *args, **params)

    api.call_action = theirs
    install(api, owner, wrapping("ours", log))
    asyncio.run(api.call_action("send_msg"))
    assert log == ["ours", "theirs"]
    assert remove(api, owner) and api.call_action is theirs
    install(api, owner, wrapping("ours", log))
    api.call_action = wrapping("top", log)(api.call_action)  # wrapped on top of ours
    assert not remove(api, owner)


@pytest.mark.parametrize("action", sorted(MESSAGE_ACTIONS))
def test_secret_in_text_is_seen(action):
    assert SECRET in "".join(texts(action, {"message": [{"type": "text", "data": {"text": SECRET}}]}))
