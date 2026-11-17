from __future__ import annotations

import asyncio
import contextvars
import sys
import time
import weakref
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from . import gate as send_gate
from . import selfcheck, sensitive
from .convert import convert

PLUGIN_NAME = "astrbot_plugin_qq_pretty"
_DEFAULT_PLATFORMS = ("aiocqhttp",)
# The lowest decorating priority, so it sees the final text (another hook at this same priority may share it).
CHECK_PRIORITY = -sys.maxsize
# How often the secrets in AstrBot's config files are read again, so a key added later is covered too.
SECRETS_REFRESH_SEC = 300
# Platform adapters whose client is an aiocqhttp CQHttp; the send gate sits on its `api.call_action`.
ONEBOT_ADAPTERS = ("aiocqhttp",)
# The reply text the reply check just logged in this task, so the send gate in log mode does not log the
# same reply again as the pipeline sends it. A task spawned later inherits it, so the gate stays quiet only
# when what it sends is part of that text: a mismatch costs a duplicate line, never a missing one.
_REPLY_LOGGED: contextvars.ContextVar[tuple[float, str]] = contextvars.ContextVar(
    "qq_pretty_reply_logged", default=(0.0, ""))
REPLY_LOGGED_TTL_SEC = 120.0
# Hit counts for the admin command, in plugin_data. Written a while after a hit, and at terminate.
STATS_FILE = "hit_stats.json"
STATS_SAVE_DELAY_SEC = 30.0


class _Pass:
    """The gate's verdict: send the call unchanged."""


class _Hold:
    """The gate's verdict: send nothing."""


PASS = _Pass()
HOLD = _Hold()


def _astrbot_data_dir() -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        return Path(get_astrbot_data_path())
    except Exception:
        return Path.cwd() / "data"


def _plugin_data_dir() -> Path:
    try:
        from astrbot.api.star import StarTools

        return Path(StarTools.get_data_dir(PLUGIN_NAME))
    except Exception:
        return _astrbot_data_dir() / "plugin_data" / PLUGIN_NAME


class QQPrettyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self._config_secrets: dict[str, str] = {}
        self._secrets_read_at: float | None = None
        # Every client this instance gated, a reloaded platform's old one too, so terminate takes each off.
        self._gated_apis: weakref.WeakSet = weakref.WeakSet()
        # Turned off at terminate, so a gate that could not be taken off passes everything through.
        self._gate_live = True
        self._stats: selfcheck.HitStats | None = None
        self._stats_timer: asyncio.TimerHandle | None = None
        self._stats_loop: asyncio.AbstractEventLoop | None = None

    async def initialize(self) -> None:
        if self._protect_config_secrets(self._check_section()):
            await self._read_config_secrets(announce=True)
        try:
            self._stats = await asyncio.to_thread(self._load_stats)
            # A first file now, so the counts' start time survives the next reload.
            await asyncio.to_thread(self._flush_stats)
        except Exception:
            logger.exception("%s: loading hit stats failed", PLUGIN_NAME)
        self._install_send_gates("initialize")

    @filter.on_platform_loaded()
    async def on_platform_loaded(self) -> None:
        # A platform (re)load builds a new client; put the gate on it too.
        self._install_send_gates("platform loaded")

    async def terminate(self) -> None:
        self._remove_send_gates()
        if self._stats_timer is not None:
            self._stats_timer.cancel()
            self._stats_timer = None
        self._flush_stats()

    # ---- hit counts --------------------------------------------------------------------------

    @staticmethod
    def _load_stats() -> selfcheck.HitStats:
        stats = selfcheck.HitStats.load(_plugin_data_dir() / STATS_FILE)
        if stats.load_error:
            logger.warning("qq-pretty: hit stats unreadable (%s), counting from now", stats.load_error)
        return stats

    def _hit_stats(self) -> selfcheck.HitStats:
        if self._stats is None:
            self._stats = self._load_stats()
        return self._stats

    def _flush_stats(self) -> None:
        stats = self._stats
        if stats is None or not stats.dirty:
            return
        try:
            stats.save()
        except Exception:
            logger.exception("%s: saving hit stats failed", PLUGIN_NAME)

    def _on_stats_timer(self) -> None:
        self._stats_timer = None
        self._flush_stats()

    def _schedule_stats_save(self) -> None:
        # A timer from a loop that has since closed never fires; only a live one makes another unneeded.
        if self._stats_timer is not None and self._stats_loop is not None and not self._stats_loop.is_closed():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush_stats()
            return
        self._stats_loop = loop
        self._stats_timer = loop.call_later(STATS_SAVE_DELAY_SEC, self._on_stats_timer)

    def _count_hit(self, source: str, hits: list[sensitive.Hit], action: str) -> None:
        """Count a real hit for the admin command; counting never gets in the way of the check."""
        try:
            self._hit_stats().record(source, [h.rule for h in hits], action)
        except Exception:
            logger.exception("%s: counting a hit failed", PLUGIN_NAME)
            return
        self._schedule_stats_save()

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def _enabled(self) -> bool:
        return self._cfg("enabled", True) is not False

    def _llm_only(self) -> bool:
        return self._cfg("llm_only", True) is not False

    def _platforms(self) -> set[str]:
        raw = self._cfg("platforms", list(_DEFAULT_PLATFORMS))
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, list):
            items = raw
        else:
            items = list(_DEFAULT_PLATFORMS)
        names = {str(x).strip() for x in items if str(x).strip()}
        return names or set(_DEFAULT_PLATFORMS)

    def _heading_style(self) -> str:
        style = str(self._cfg("heading_style", "bracket") or "bracket")
        return style if style in {"bracket", "plain"} else "bracket"

    def _code_block_style(self) -> str:
        style = str(self._cfg("code_block_style", "label") or "label")
        return style if style in {"label", "indent", "keep"} else "label"

    def _keep_inline_code(self) -> bool:
        return self._cfg("keep_inline_code", False) is True

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not self._enabled():
            return
        if event.get_platform_name() not in self._platforms():
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        if self._llm_only() and not result.is_llm_result():
            return

        heading_style = self._heading_style()
        code_block_style = self._code_block_style()
        keep_inline = self._keep_inline_code()
        changed = False
        for comp in result.chain:
            if not isinstance(comp, Plain):
                continue
            original = comp.text or ""
            if not original:
                continue
            try:
                cleaned = convert(
                    original,
                    heading_style=heading_style,  # type: ignore[arg-type]
                    code_block_style=code_block_style,  # type: ignore[arg-type]
                    keep_inline_code=keep_inline,
                )
            except Exception:
                logger.exception("%s: convert failed, keep original", PLUGIN_NAME)
                continue
            if cleaned and cleaned != original:
                comp.text = cleaned
                changed = True
        if changed:
            logger.debug("%s: stripped markdown for QQ", PLUGIN_NAME)

    # ---- last outbound check -----------------------------------------------------------

    def _check_section(self) -> dict:
        raw = self._cfg("outbound_check", {})
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _protect_config_secrets(section: dict) -> bool:
        return section.get("protect_config_secrets", True) is not False

    async def _read_config_secrets(self, announce: bool = False) -> None:
        # Stamp first: a failing read is retried at the next refresh, not on every message.
        self._secrets_read_at = time.monotonic()
        try:
            files = sensitive.config_files(_astrbot_data_dir())
            secrets, parsed = await asyncio.to_thread(sensitive.load_config_secrets, files)
        except Exception:
            logger.exception("%s: reading config secrets failed", PLUGIN_NAME)
            return
        changed = len(secrets) != len(self._config_secrets)
        self._config_secrets = secrets
        if announce or changed:
            # Counts only: never a value, never a key name.
            logger.info("qq-pretty: protecting %d config secrets from %d config files", len(secrets), parsed)

    async def _config_secrets_now(self, section: dict) -> dict[str, str]:
        if not self._protect_config_secrets(section):
            return {}
        read_at = self._secrets_read_at
        if read_at is None or time.monotonic() - read_at >= SECRETS_REFRESH_SEC:
            await self._read_config_secrets()
        return self._config_secrets

    @staticmethod
    def _plain_text(parts: list[Plain]) -> str:
        # Text segments of one message are shown joined, so a secret cut across two is checked whole.
        return "".join(str(comp.text or "") for comp in parts)

    @filter.on_decorating_result(priority=CHECK_PRIORITY)
    async def check_outbound(self, event: AstrMessageEvent) -> None:
        """Every reply about to go out, model or not: log a hit; in mask mode hide it; in drop mode hold it."""
        section = self._check_section()
        mode = sensitive.mode(section.get("mode"))
        if mode == "off" or event.get_platform_name() not in self._platforms():
            return
        result = event.get_result()
        if result is None or not result.chain:
            return
        parts = [comp for comp in result.chain if isinstance(comp, Plain)]
        text = self._plain_text(parts)
        if not text.strip():
            return
        try:
            known = sensitive.known_list(section.get("known_secrets"))
            config_secrets = await self._config_secrets_now(section)
            rules = sensitive.rules_from_config(section)
            hits = sensitive.scan(text, known, rules, config_secrets)
        except Exception:
            logger.exception("%s: outbound check failed", PLUGIN_NAME)
            return
        if not hits:
            return
        logger.warning("qq-pretty: outbound hit mode=%s rules=%s samples=%s llm=%s chars=%s",
                       mode, ",".join(h.rule for h in hits), " ".join(h.sample for h in hits),
                       int(bool(result.is_llm_result())), len(text))
        if mode == "log":
            _REPLY_LOGGED.set((time.monotonic(), text))
            self._count_hit("reply", hits, "logged")
            return
        if mode == "mask":
            placeholder = self._placeholder(section)
            secrets = (*known, *config_secrets)
            try:
                for comp in parts:
                    comp.text = sensitive.redact(str(comp.text or ""), secrets, rules, placeholder)
                left = sensitive.scan(self._plain_text(parts), known, rules, config_secrets)
            except Exception:
                logger.exception("%s: masking failed, holding the reply", PLUGIN_NAME)
                left = hits
            if not left:
                self._count_hit("reply", hits, "masked")
                return
            # A match split across two text segments cannot be masked piece by piece: hold the reply.
            logger.warning("qq-pretty: mask left rules=%s, holding the reply", ",".join(h.rule for h in left))
        self._hold(result, section)
        self._count_hit("reply", hits, "held")

    @staticmethod
    def _hold(result, section: dict) -> None:
        """Send nothing, or only the configured notice. An empty chain is never sent and never fires
        after_message_sent, so with no notice nothing at all is said in the chat."""
        notice = str(section.get("drop_notice") or "").strip()
        result.chain.clear()
        if notice:
            result.chain.append(Plain(notice))

    @staticmethod
    def _placeholder(section: dict) -> str:
        return str(section.get("mask_text") or "").strip() or sensitive.DEFAULT_MASK

    # ---- send gate: every OneBot send, not only pipeline replies --------------------------------

    def _gate_wanted(self) -> bool:
        section = self._check_section()
        return (section.get("check_direct_sends", True) is not False
                and any(name in self._platforms() for name in ONEBOT_ADAPTERS))

    def _onebot_apis(self) -> list[Any]:
        manager = getattr(self.context, "platform_manager", None)
        apis = []
        for inst in list(getattr(manager, "platform_insts", None) or []):
            try:
                if inst.meta().name not in ONEBOT_ADAPTERS:
                    continue
                bot = inst.get_client() if hasattr(inst, "get_client") else getattr(inst, "bot", None)
                api = getattr(bot, "api", None)
            except Exception:
                continue
            if api is not None and callable(getattr(api, "call_action", None)):
                apis.append(api)
        return apis

    def _install_send_gates(self, why: str) -> None:
        if not self._gate_wanted():
            return
        added = 0
        apis = self._onebot_apis()
        for api in apis:
            try:
                if send_gate.install(api, self, self._make_gate):
                    added += 1
                if send_gate.owner_of(api) is self:
                    self._gated_apis.add(api)
            except Exception:
                logger.exception("%s: send gate install failed", PLUGIN_NAME)
        if added:
            live = sum(1 for api in apis if send_gate.owner_of(api) is self)
            logger.info("qq-pretty: send gate installed on %d OneBot client(s) (%s)", live, why)
        elif not apis:
            logger.info("qq-pretty: send gate waiting, no OneBot client yet (%s)", why)

    def _remove_send_gates(self) -> None:
        self._gate_live = False
        kept = 0
        for api in list(self._gated_apis):
            try:
                if not send_gate.remove(api, self):
                    kept += 1
            except Exception:
                kept += 1
                logger.exception("%s: send gate remove failed", PLUGIN_NAME)
        self._gated_apis.clear()
        if kept:
            logger.warning("qq-pretty: send gate left on %d client(s), now passing everything through", kept)

    def _make_gate(self, original):
        async def gate(*args, **params):
            action = args[0] if args else params.get("action")
            # call_action takes the action plus keywords; anything else is passed on untouched.
            if not self._gate_live or len(args) > 1 or not send_gate.checked(action):
                return await original(*args, **params)
            call = {k: v for k, v in params.items() if k != "action"}
            try:
                verdict = await self._judge_send(action, call)
            except Exception:
                logger.exception("%s: send gate check failed, sending as is", PLUGIN_NAME)
                verdict = PASS
            if verdict is PASS:
                return await original(*args, **params)
            if verdict is HOLD:
                return None  # never raise: the framework would log the held message in its error line
            new_action, new_params = verdict
            return await original(new_action, **new_params)

        return gate

    @staticmethod
    def _reply_already_logged(mode: str, text: str) -> bool:
        if mode != "log":
            return False  # masking or holding is always logged
        at, logged = _REPLY_LOGGED.get()
        part = text.strip()
        return bool(logged and part) and time.monotonic() - at <= REPLY_LOGGED_TTL_SEC and part in logged

    @staticmethod
    def _log_send_hit(action: str, mode: str, hits: list[sensitive.Hit], text: str) -> None:
        logger.warning("qq-pretty: send-layer hit action=%s caller=%s mode=%s rules=%s samples=%s chars=%s",
                       action, send_gate.plugin_of(sys._getframe(), PLUGIN_NAME), mode,
                       ",".join(h.rule for h in hits), " ".join(h.sample for h in hits), len(text))

    async def _judge_send(self, action: str, params: dict):
        section = self._check_section()
        mode = sensitive.mode(section.get("mode"))
        if mode == "off" or section.get("check_direct_sends", True) is False:
            return PASS
        text = "\n".join(send_gate.texts(action, params))
        if not text.strip():
            return PASS
        known = sensitive.known_list(section.get("known_secrets"))
        config_secrets = await self._config_secrets_now(section)
        rules = sensitive.rules_from_config(section)
        hits = sensitive.scan(text, known, rules, config_secrets)
        if not hits:
            return PASS
        if mode == "log":
            # The reply check already logged and counted this reply: one hit, one line, one count.
            if not self._reply_already_logged(mode, text):
                self._log_send_hit(action, mode, hits, text)
                self._count_hit("send", hits, "logged")
            return PASS
        self._log_send_hit(action, mode, hits, text)
        if mode == "mask":
            secrets = (*known, *config_secrets)
            placeholder = self._placeholder(section)
            masked = send_gate.rewrite(action, params,
                                       lambda s: sensitive.redact(s, secrets, rules, placeholder))
            left = sensitive.scan("\n".join(send_gate.texts(action, masked)), known, rules, config_secrets)
            if not left:
                self._count_hit("send", hits, "masked")
                return action, masked
            logger.warning("qq-pretty: send-layer mask left rules=%s, holding action=%s",
                           ",".join(h.rule for h in left), action)
        self._count_hit("send", hits, "held")
        notice = send_gate.notice_params(action, params, str(section.get("drop_notice") or "").strip())
        return (action, notice) if notice is not None else HOLD

    # ---- admin command: state, hit counts, a dry run --------------------------------------------

    # Admins only, and silent for everyone else (raise_error=False): a member's 「/防泄密」 is ordinary chat,
    # and the command stays out of menus built from the commands members can use.
    @filter.command("outbound_guard", alias={"防泄密"})
    @filter.permission_type(filter.PermissionType.ADMIN, raise_error=False)
    async def outbound_guard(self, event: AstrMessageEvent):
        """防泄密自检（仅管理员）：看检查状态和命中统计；测试 <文本> 看会命中哪些规则；清零 重新统计。"""
        try:
            message = str(event.get_message_str() or "")
        except Exception:
            message = ""
        action, rest = selfcheck.parse_command(message)
        try:
            if action == "test":
                reply = await self._test_reply(rest)
            elif action == "reset":
                reply = self._reset_reply()
            elif action == "status":
                reply = await self._status_reply()
            else:
                reply = selfcheck.USAGE
        except Exception:
            logger.exception("%s: outbound_guard command failed", PLUGIN_NAME)
            reply = "自检出错了，详情看日志。"
        yield event.plain_result(reply)

    async def _status_reply(self) -> str:
        section = self._check_section()
        if not self._gate_wanted():
            gate = "关"
        else:
            live = sum(1 for api in self._onebot_apis() if send_gate.owner_of(api) is self)
            gate = f"开，装在 {live} 个 OneBot 连接上" if live else "开，但还没装上（没有 OneBot 连接）"
        config_secrets = None
        if self._protect_config_secrets(section):
            config_secrets = len(await self._config_secrets_now(section))
        lists = {
            "known": len(sensitive.known_list(section.get("known_secrets"))),
            "hosts": len(sensitive.entries(section.get("internal_hosts"))),
            "paths": len(sensitive.entries(section.get("private_paths"))),
            "names": len(sensitive.entries(section.get("internal_names"))),
        }
        stats = self._hit_stats()
        self._flush_stats()  # what the admin sees is what a restart would keep
        return selfcheck.status_text(sensitive.mode(section.get("mode")), gate, config_secrets, lists, stats)

    async def _test_reply(self, text: str) -> str:
        """The same scan a real send gets, on the admin's text. Not counted; the log line has rule names only."""
        if not text.strip():
            return selfcheck.TEST_USAGE
        section = self._check_section()
        mode = sensitive.mode(section.get("mode"))
        known = sensitive.known_list(section.get("known_secrets"))
        config_secrets = await self._config_secrets_now(section)
        rules = sensitive.rules_from_config(section)
        hits = sensitive.scan(text, known, rules, config_secrets)
        placeholder = self._placeholder(section)
        spans, left = 0, False
        if hits:
            secrets = (*known, *config_secrets)
            spans = len(sensitive.find_spans(text, secrets, rules))
            left = bool(sensitive.scan(sensitive.redact(text, secrets, rules, placeholder), known, rules,
                                       config_secrets))
        logger.info("qq-pretty: self-test mode=%s rules=%s chars=%s", mode,
                    ",".join(h.rule for h in hits) or "-", len(text))
        return selfcheck.describe_test([h.rule for h in hits], mode, spans, left, placeholder,
                                       bool(str(section.get("drop_notice") or "").strip()))

    def _reset_reply(self) -> str:
        stats = self._hit_stats()
        stats.reset()
        self._flush_stats()
        logger.info("qq-pretty: hit stats reset")
        return selfcheck.reset_text(stats)
