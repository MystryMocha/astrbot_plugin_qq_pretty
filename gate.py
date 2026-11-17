"""Send-layer gate: the text an outgoing OneBot call shows, and the same call with that text rewritten.

Pure Python with no AstrBot or aiocqhttp import, so it is tested offline. Only display text is read:
text and markdown segments, forward-node names and contents, file names and a few NapCat text fields.
Media `file` values (base64 images, local paths, URLs) are transport, not display; they are never read
or copied.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

MESSAGE_ACTIONS = frozenset({"send_msg", "send_group_msg", "send_private_msg"})
FORWARD_ACTIONS = frozenset({"send_forward_msg", "send_group_forward_msg", "send_private_forward_msg"})
# Actions whose display text is a plain top-level field.
FIELD_ACTIONS: dict[str, tuple[str, ...]] = {
    "upload_group_file": ("name",),
    "upload_private_file": ("name",),
    "_send_group_notice": ("content",),
    "send_group_ai_record": ("text",),
}
# Forward cards may carry their own outer text (NapCat).
_FORWARD_FIELDS = ("prompt", "summary", "source")

# Set on a gate function: the owner that installed it, and the call_action it wraps.
MARK = "_qq_pretty_gate"
ORIGINAL = "_qq_pretty_original"

_CQ_CODE = re.compile(r"\[CQ:[^\]]*\]")

Rewrite = Callable[[str], str]


def checked(action: Any) -> bool:
    return isinstance(action, str) and (action in MESSAGE_ACTIONS or action in FORWARD_ACTIONS
                                        or action in FIELD_ACTIONS)


# ---- reading ------------------------------------------------------------------------------------


def texts(action: str, params: Mapping[str, Any]) -> list[str]:
    """Display units of one call: text segments of one message joined, then each name or field alone."""
    if action in MESSAGE_ACTIONS:
        units = _message_texts(params.get("message"), bool(params.get("auto_escape")))
    elif action in FORWARD_ACTIONS:
        units = _forward_texts(params)
    else:
        units = [_str(params.get(key)) for key in FIELD_ACTIONS.get(action, ())]
    return [u for u in units if u]


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _data(seg: Mapping[str, Any]) -> Mapping[str, Any]:
    data = seg.get("data")
    return data if isinstance(data, Mapping) else {}


def _unescape(text: str) -> str:
    return text.replace("&#91;", "[").replace("&#93;", "]").replace("&#44;", ",").replace("&amp;", "&")


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")


def _message_texts(message: Any, auto_escape: bool = False) -> list[str]:
    if isinstance(message, str):
        return [message if auto_escape else _unescape(_CQ_CODE.sub("", message))]
    if isinstance(message, Mapping):  # a single segment
        message = [message]
    if not isinstance(message, (list, tuple)):
        return []
    run: list[str] = []
    units: list[str] = []
    for seg in message:
        if not isinstance(seg, Mapping):
            continue
        kind = seg.get("type")
        data = _data(seg)
        if kind == "text":
            run.append(_str(data.get("text")))
        elif kind == "markdown":
            run.append(_str(data.get("content")))
        elif kind == "file":
            units.append(_str(data.get("name")))
        elif kind == "node":
            units.extend(_node_texts(data))
        elif kind is None and isinstance(seg.get("messages"), list):  # AstrBot's nested Nodes
            units.extend(_nodes_texts(seg["messages"]))
    return ["".join(run), *units]


def _node_texts(data: Mapping[str, Any]) -> list[str]:
    units = [_str(data.get("name")), _str(data.get("nickname"))]
    if "content" in data:
        units.extend(_message_texts(data.get("content")))
    return units


def _nodes_texts(nodes: Any) -> list[str]:
    units: list[str] = []
    for node in nodes if isinstance(nodes, (list, tuple)) else ():
        if isinstance(node, Mapping) and node.get("type") == "node":
            units.extend(_node_texts(_data(node)))
    return units


def _forward_texts(params: Mapping[str, Any]) -> list[str]:
    units = _nodes_texts(params.get("messages"))
    news = params.get("news")
    for item in news if isinstance(news, (list, tuple)) else ():
        if isinstance(item, Mapping):
            units.append(_str(item.get("text")))
    units.extend(_str(params.get(key)) for key in _FORWARD_FIELDS)
    return units


# ---- rewriting ----------------------------------------------------------------------------------


def rewrite(action: str, params: Mapping[str, Any], fn: Rewrite) -> dict[str, Any]:
    """The same call with every display text passed through fn. Only what holds text is copied; the
    caller's own lists and dicts are never changed, and media segments are passed on as they are."""
    out = dict(params)
    if action in MESSAGE_ACTIONS:
        if "message" in out:
            out["message"] = _rewrite_message(out["message"], fn, bool(out.get("auto_escape")))
    elif action in FORWARD_ACTIONS:
        if "messages" in out:
            out["messages"] = _rewrite_nodes(out["messages"], fn)
        news = out.get("news")
        if isinstance(news, (list, tuple)):
            out["news"] = [{**item, "text": fn(item["text"])} if isinstance(item, Mapping)
                           and isinstance(item.get("text"), str) else item for item in news]
        for key in _FORWARD_FIELDS:
            if isinstance(out.get(key), str):
                out[key] = fn(out[key])
    else:
        for key in FIELD_ACTIONS.get(action, ()):
            if isinstance(out.get(key), str):
                out[key] = fn(out[key])
    return out


def _rewrite_cq(message: str, fn: Rewrite) -> str:
    """Rewrite the text between CQ codes; the codes themselves are kept as they are."""
    out: list[str] = []
    pos = 0
    for code in _CQ_CODE.finditer(message):
        out.append(_rewrite_cq_text(message[pos:code.start()], fn))
        out.append(code.group(0))
        pos = code.end()
    out.append(_rewrite_cq_text(message[pos:], fn))
    return "".join(out)


def _rewrite_cq_text(part: str, fn: Rewrite) -> str:
    if not part:
        return part
    plain = _unescape(part)
    new = fn(plain)
    return part if new == plain else _escape(new)


def _rewrite_message(message: Any, fn: Rewrite, auto_escape: bool = False) -> Any:
    if isinstance(message, str):
        return fn(message) if auto_escape else _rewrite_cq(message, fn)
    if isinstance(message, Mapping):
        return _rewrite_segment(message, fn)
    if isinstance(message, (list, tuple)):
        return [_rewrite_segment(seg, fn) for seg in message]
    return message


def _with_data(seg: Mapping[str, Any], key: str, fn: Rewrite) -> Any:
    data = _data(seg)
    if not isinstance(data.get(key), str):
        return seg
    return {**seg, "data": {**data, key: fn(data[key])}}


def _rewrite_segment(seg: Any, fn: Rewrite) -> Any:
    if not isinstance(seg, Mapping):
        return seg
    kind = seg.get("type")
    if kind == "text":
        return _with_data(seg, "text", fn)
    if kind == "markdown":
        return _with_data(seg, "content", fn)
    if kind == "file":
        return _with_data(seg, "name", fn)
    if kind == "node":
        return _rewrite_node(seg, fn)
    if kind is None and isinstance(seg.get("messages"), list):
        return {**seg, "messages": _rewrite_nodes(seg["messages"], fn)}
    return seg


def _rewrite_node(node: Mapping[str, Any], fn: Rewrite) -> Any:
    data = dict(_data(node))
    for key in ("name", "nickname"):
        if isinstance(data.get(key), str):
            data[key] = fn(data[key])
    if "content" in data:
        data["content"] = _rewrite_message(data["content"], fn)
    return {**node, "data": data}


def _rewrite_nodes(nodes: Any, fn: Rewrite) -> Any:
    if not isinstance(nodes, (list, tuple)):
        return nodes
    return [_rewrite_node(n, fn) if isinstance(n, Mapping) and n.get("type") == "node" else n for n in nodes]


def notice_params(action: str, params: Mapping[str, Any], notice: str) -> dict[str, Any] | None:
    """The held call turned into the notice, same target and same self_id. Only plain message calls
    get one: a forward or an upload is held without a word."""
    if action not in MESSAGE_ACTIONS or not notice:
        return None
    out = {k: v for k, v in params.items() if k != "auto_escape"}
    out["message"] = [{"type": "text", "data": {"text": notice}}]
    return out


# ---- who sent it ---------------------------------------------------------------------------------


def plugin_of(frame: Any, own: str) -> str:
    """The plugin whose code made this call: the first plugin module up the stack other than `own`.
    `astrbot` when only framework code is found (the reply pipeline), `unknown` otherwise."""
    framework = False
    while frame is not None:
        parts = str(frame.f_globals.get("__name__") or "").split(".")
        if own not in parts:
            if len(parts) > 2 and parts[0] == "data" and parts[1] == "plugins":
                return parts[2]
            if len(parts) > 2 and parts[0] == "astrbot" and parts[1] == "builtin_stars":
                return parts[2]
            if any(p.startswith("astrbot_plugin_") for p in parts):
                return next(p for p in parts if p.startswith("astrbot_plugin_"))
            framework = framework or parts[0] == "astrbot"
        frame = frame.f_back
    return "astrbot" if framework else "unknown"


# ---- installing ---------------------------------------------------------------------------------


def owner_of(api: Any) -> Any:
    """The owner of the gate sitting on api.call_action, or None."""
    fn = vars(api).get("call_action")
    return getattr(fn, MARK, None) if fn is not None else None


def install(api: Any, owner: Any, make: Callable[[Callable[..., Any]], Callable[..., Any]]) -> bool:
    """Put make(original) on api.call_action as an instance attribute. False when this owner's gate is
    already there. A gate left by an earlier load of the plugin is replaced, not wrapped again."""
    current = vars(api).get("call_action")
    if current is not None and getattr(current, MARK, None) is owner:
        return False
    if current is not None and getattr(current, MARK, None) is not None:
        original = getattr(current, ORIGINAL)
    elif current is not None:
        original = current  # someone else's wrapper: go on top of it
    else:
        original = api.call_action
    gate = make(original)
    setattr(gate, MARK, owner)
    setattr(gate, ORIGINAL, original)
    api.call_action = gate
    return True


def remove(api: Any, owner: Any) -> bool:
    """Put back what the gate wrapped. False when the top of api.call_action is not this owner's gate
    (someone wrapped on top since); the owner then makes its gate pass everything through."""
    current = vars(api).get("call_action")
    if current is None or getattr(current, MARK, None) is not owner:
        return False
    original = getattr(current, ORIGINAL)
    if getattr(original, "__self__", None) is api and \
            getattr(original, "__func__", None) is getattr(type(api), "call_action", None):
        del api.call_action
    else:
        api.call_action = original
    return True
