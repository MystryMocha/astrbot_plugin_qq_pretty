"""Tiny astrbot stubs so main.py imports offline; the real framework is only in the production image."""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT.parent):  # `sensitive` / `convert` as top-level modules, `main` inside its package
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _install_stubs() -> None:
    try:
        import astrbot  # noqa: F401

        return
    except ImportError:
        pass

    def mod(name: str) -> types.ModuleType:
        module = sys.modules.get(name) or types.ModuleType(name)
        sys.modules[name] = module
        return module

    mod("astrbot")
    mod("astrbot.api").logger = logging.getLogger("qq-pretty-test")

    class _Filter:
        @staticmethod
        def on_decorating_result(**kwargs):
            def deco(fn):
                fn._test_priority = kwargs.get("priority", 0)
                return fn

            return deco

        @staticmethod
        def on_platform_loaded(**kwargs):
            return lambda fn: fn

        @staticmethod
        def command(name, alias=None, **kwargs):
            def deco(fn):
                fn._test_command = (name, set(alias or ()))
                return fn

            return deco

        class PermissionType:
            ADMIN = "admin"
            MEMBER = "member"

        @staticmethod
        def permission_type(permission, raise_error=True):
            def deco(fn):
                fn._test_permission = (permission, raise_error)
                return fn

            return deco

    class AstrMessageEvent:
        pass

    mod("astrbot.api.event").filter = _Filter()
    mod("astrbot.api.event").AstrMessageEvent = AstrMessageEvent

    class Star:
        def __init__(self, context=None):
            self.context = context

    star = mod("astrbot.api.star")
    star.Star = Star
    star.Context = object
    mod("astrbot.core").AstrBotConfig = dict

    class Plain:
        def __init__(self, text: str = ""):
            self.text = text

    class Image:
        def __init__(self, url: str = ""):
            self.url = url

    components = mod("astrbot.core.message.components")
    components.Plain = Plain
    components.Image = Image
    mod("astrbot.core.message")
    mod("astrbot.core.platform")
    mod("astrbot.core.platform.astr_message_event").AstrMessageEvent = AstrMessageEvent


_install_stubs()
