"""Last outbound check: server paths, internal addresses and credentials in a reply about to go out.

The rule set follows qq-bridge `src/sensitive.js` (notice below): heuristics for paths and assigned
credentials plus exact lists of known secrets (a bare 32-char hex key is invisible to any pattern). The
built-in rules know only common AstrBot / OneBot deployments; a site's own hosts, paths and names come from
three literal lists in the plugin config, and the secrets already in AstrBot's own config files are picked up
by key name. `log` only logs a hit, `mask` replaces each matched span and sends the rest, `drop` sends
nothing (or a fixed notice). Nothing about a hit is said in the chat beyond that, and logs carry only rule
names, masked samples and, for config secrets, the file and key name the value came from.

Every pattern is compiled with `re.ASCII`: in Unicode mode CJK characters are word characters, so `\\b` and
`(?<!\\w)` never fire between 「在」 and `/data/...`.

Portions adapted from qq-bridge (https://github.com/Derpyu520/qq-bridge, commit a49877f, MIT):

    Copyright (c) 2026 Derpyu520 and qq-bridge / QSH contributors

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

MODES = ("off", "log", "mask", "drop")
DEFAULT_MODE = "log"
DEFAULT_MASK = "[已隐藏]"
MIN_KNOWN = 8  # shorter "known secrets" would match ordinary text
MIN_NAME = 3  # a bare name matches anywhere in the text; shorter ones hit ordinary words
SAMPLE_KEEP = 4
MAX_CONFIG_BYTES = 5 * 1024 * 1024

_EXACT = re.ASCII  # token shapes: `AKIA` and `eyJ` are case-significant
_ANY_CASE = re.ASCII | re.IGNORECASE
# What a trailing `*` in a config entry stands for: the rest of a host or name, or the rest of a path segment.
_NAME_TAIL = r"[A-Za-z0-9\-]*"
_PATH_TAIL = r"[^\s/\"'<>]*"

# Built-in hosts and paths: common AstrBot / OneBot deployment names only. A site adds its own in the config.
_BUILTIN_HOSTS = (
    r"astrbot", r"napcat", r"llonebot", r"lagrange", r"localhost", r"127\.0\.0\.1", r"0\.0\.0\.0",
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}", r"192\.168\.\d{1,3}\.\d{1,3}", r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}",
)
_BUILTIN_PATHS = (
    r"/data/(?:astrbot|napcat|llonebot|lagrange|backups)(?!\w)", r"/AstrBot/(?:data|astrbot)(?!\w)",
    r"/root/", r"/home/[a-z_][a-z0-9_\-]*/", r"/etc/(?:shadow|sudoers)(?!\w)", r"plugin_data/",
)

# Service token shapes, by prefix. Lengths are kept realistic so `sk-learn` or `hf_hub` stay ordinary words.
_TOKEN_PREFIXES = (
    r"sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}",  # OpenAI, Anthropic and the many OpenAI-compatible services
    r"gh[pousr]_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}", r"glpat-[A-Za-z0-9_\-]{20,}",
    r"AKIA[0-9A-Z]{16}",  # AWS access key id
    r"AKID[0-9A-Za-z]{32}",  # Tencent Cloud SecretId
    r"LTAI[0-9A-Za-z]{12,24}",  # Alibaba Cloud AccessKey id
    r"AKLT[0-9A-Za-z_\-]{20,}",  # Volcengine access key id
    r"AIza[0-9A-Za-z_\-]{30,}", r"ya29\.[0-9A-Za-z_\-]{20,}",  # Google API key, OAuth access token
    r"xox[abprs]-[A-Za-z0-9\-]{10,}",  # Slack
    r"hf_[A-Za-z0-9]{30,}",  # Hugging Face
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}",  # Stripe secret / restricted keys
    r"xai-[A-Za-z0-9]{20,}", r"gsk_[A-Za-z0-9]{20,}",  # xAI, Groq
    r"npm_[A-Za-z0-9]{36}",
)

_TOKEN_RULES: tuple[tuple[str, str, int], ...] = (
    ("private_key", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY", _EXACT),
    ("token_prefix", r"\b(?:" + "|".join(_TOKEN_PREFIXES) + ")", _EXACT),
    # Telegram (`123456789:AA…`, 35 characters after the colon) and Discord (three dot-separated parts).
    ("bot_token",
     r"\b\d{8,10}:[A-Za-z0-9_\-]{35}(?![A-Za-z0-9_\-])|\b[MN][A-Za-z0-9]{23,25}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}",
     _EXACT),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", _EXACT),
    # Only an assignment with a long value: "token 是多少" or "password 忘了" is ordinary chat.
    ("assigned_secret",
     r"\b(?:api[_\-]?key|secret(?:[_\-]?key)?|access[_\-]?token|auth[_\-]?token|token|password|passwd"
     r"|bearer)\b[\"']?\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9_\-./+]{16,})", _ANY_CASE),
    # `scheme://user:password@host`, for any scheme (redis://, mysql://, https://...).
    ("url_credentials", r"\b[A-Za-z][A-Za-z0-9+.\-]*://(?P<value>[^\s/:@]+:[^\s/@]+)@", _ANY_CASE),
)

# Config keys whose string values are secrets. Matched on the lowercased key with `-` read as `_`.
_SECRET_KEYS = frozenset({
    "apikey", "api_key", "api_keys", "secret", "secret_key", "secretkey", "client_secret",
    "app_secret", "appsecret", "jwt_secret", "token", "access_token", "auth_token", "bot_token",
    "refresh_token", "ws_reverse_token", "password", "passwd", "pwd", "private_key", "access_key",
    "access_key_secret", "secret_access_key", "app_key", "appkey",
})
_SECRET_KEY_SUFFIXES = ("_api_key", "_apikey", "_secret", "_secret_key", "_token", "_password", "_passwd",
                        "_access_key")
# A bare `key` / `keys` is AstrBot's provider API key list, but a keyword-reply plugin may use the same
# name for trigger words, so those values must also look like a key: long, with both letters and digits.
_GENERIC_KEYS = frozenset({"key", "keys"})
_SECRET_VALUE = re.compile(r"[\x21-\x7e]{%d,}" % MIN_KNOWN)  # secrets are printable ASCII without spaces
_KEY_LIKE = re.compile(r"(?=.*[A-Za-z])(?=.*\d)[\x21-\x7e]{16,}")

# How far a masked path reaches past the matched prefix: the rest of the path, stopping at CJK text.
_PATH_REST = re.compile(r"[A-Za-z0-9_.\-/\\~%+@=:]*", re.ASCII)
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----", re.ASCII)


@dataclass
class Hit:
    rule: str
    sample: str  # masked


def mode(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in MODES else DEFAULT_MODE


def entries(raw: object) -> tuple[str, ...]:
    """A config list (or a single string) as stripped, de-duplicated, non-empty strings, longest first."""
    items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, str) else [])
    cleaned = {str(x).strip() for x in items if x is not None}
    return tuple(sorted((s for s in cleaned if s), key=lambda s: (-len(s), s)))


def known_list(raw: object) -> list[str]:
    return [s for s in entries(raw) if len(s) >= MIN_KNOWN]


def _literal(entry: str, tail: str) -> str:
    """One config entry as a regex: the text escaped, a trailing `*` turned into `tail`."""
    if entry.endswith("*"):
        return re.escape(entry.rstrip("*")) + tail
    return re.escape(entry)


def _path_literal(entry: str) -> str:
    # `/data/my-bot` must not hit `/data/my-botx`; an entry ending in `/` already stops at a separator.
    if entry.endswith(("*", "/", "\\")):
        return _literal(entry, _PATH_TAIL)
    return re.escape(entry) + r"(?!\w)"


@lru_cache(maxsize=16)
def compile_rules(hosts: tuple[str, ...] = (), paths: tuple[str, ...] = (),
                  names: tuple[str, ...] = ()) -> tuple[tuple[str, re.Pattern], ...]:
    """Built-in rules plus a site's own literal hosts, paths and names, always in the same rule order."""
    hosts = tuple(h for h in hosts if h.rstrip("*"))
    paths = tuple(p for p in paths if p.rstrip("*"))
    names = tuple(n for n in names if len(n.rstrip("*")) >= MIN_NAME)
    host_alts = "|".join((*(_literal(h, _NAME_TAIL) for h in hosts), *_BUILTIN_HOSTS))
    path_alts = "|".join((*(_path_literal(p) for p in paths), *_BUILTIN_PATHS))
    rules = [(name, re.compile(pattern, flags)) for name, pattern, flags in _TOKEN_RULES]
    # Not right after a word character or a dot: `./data/x` is relative, `example.com/data/x` a public URL.
    rules.append(("server_path", re.compile(rf"(?<![\w.])(?:{path_alts})", _ANY_CASE)))
    rules.append(("windows_user_path",
                  re.compile(r"\b[A-Z]:\\(?:Users|Documents and Settings)\\[^\\\s，。！？；：、）」】,;!?)\]\"'<>]+",
                             _ANY_CASE)))
    # The host has to end there: `http://astrbot:6185` is internal, `https://astrbot.app` is the public site.
    rules.append(("internal_url", re.compile(rf"\bhttps?://(?:{host_alts})(?!\w|\.\w)(?::\d+)?", _ANY_CASE)))
    if names:
        name_alts = "|".join(_literal(n, _NAME_TAIL) for n in names)
        rules.append(("internal_name", re.compile(rf"(?<![\w\-])(?:{name_alts})(?![\w\-])", _ANY_CASE)))
    return tuple(rules)


RULES = compile_rules()


def rules_from_config(section: dict) -> tuple[tuple[str, re.Pattern], ...]:
    """The rule set for an `outbound_check` config section."""
    return compile_rules(entries(section.get("internal_hosts")), entries(section.get("private_paths")),
                         entries(section.get("internal_names")))


def mask(text: str) -> str:
    text = str(text)
    if len(text) <= SAMPLE_KEEP:
        return "*" * len(text)
    return f"{text[:SAMPLE_KEEP]}…({len(text)})"


def scan(text: str, known: Iterable[str] = (), rules: Iterable[tuple[str, re.Pattern]] = RULES,
         config_secrets: Mapping[str, str] | None = None) -> list[Hit]:
    """Every rule that matches, one hit per rule (the first match), exact secrets first.

    Exact secrets are logged by length only (`known_secret`) or by where they came from (`config_secret`),
    never by any of their characters.
    """
    hits: list[Hit] = []
    for secret in known:
        if secret and secret in text:
            hits.append(Hit("known_secret", f"…({len(secret)})"))
            break
    for secret, source in (config_secrets or {}).items():
        if secret and secret in text:
            hits.append(Hit("config_secret", f"{source}({len(secret)})"))
            break
    for name, pattern in rules:
        found = pattern.search(text)
        if found:
            hits.append(Hit(name, mask(found.group(0))))
    return hits


# ---- mask mode -------------------------------------------------------------------------------------


def _mask_span(rule: str, found: re.Match, text: str) -> tuple[int, int]:
    """What to hide for one match: the value of an assignment, the whole path, the whole key block."""
    if "value" in found.re.groupindex and found.group("value") is not None:
        return found.span("value")
    start, end = found.span()
    if rule in ("server_path", "windows_user_path"):
        end = _PATH_REST.match(text, end).end()
    elif rule == "private_key":
        tail = _PEM_END.search(text, end)
        end = tail.end() if tail else len(text)
    return start, end


def find_spans(text: str, secrets: Iterable[str] = (),
               rules: Iterable[tuple[str, re.Pattern]] = RULES) -> list[tuple[int, int]]:
    """Every span to hide, merged and sorted: each occurrence of each exact secret, each match of each rule."""
    spans: list[tuple[int, int]] = []
    for secret in secrets:
        if not secret:
            continue
        at = text.find(secret)
        while at != -1:
            spans.append((at, at + len(secret)))
            at = text.find(secret, at + len(secret))
    for name, pattern in rules:
        for found in pattern.finditer(text):
            if found.end() > found.start():
                spans.append(_mask_span(name, found, text))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def redact(text: str, secrets: Iterable[str] = (), rules: Iterable[tuple[str, re.Pattern]] = RULES,
           placeholder: str = DEFAULT_MASK) -> str:
    """The text with every span from `find_spans` replaced by `placeholder`."""
    out: list[str] = []
    pos = 0
    for start, end in find_spans(text, secrets, rules):
        out.append(text[pos:start])
        out.append(placeholder)
        pos = end
    out.append(text[pos:])
    return "".join(out)


# ---- secrets already in AstrBot's config files ----------------------------------------------------


def _value_pattern(key: object) -> re.Pattern | None:
    """How a value under this config key must look to count as a secret, or None for an ordinary key."""
    name = str(key).strip().lower().replace("-", "_")
    if name in _GENERIC_KEYS:
        return _KEY_LIKE
    if name in _SECRET_KEYS or name.endswith(_SECRET_KEY_SUFFIXES):
        return _SECRET_VALUE
    return None


def collect_secrets(data: object) -> dict[str, str]:
    """String values under secret-looking keys, anywhere in a parsed config: {value: key name}.

    Only printable-ASCII values of at least `MIN_KNOWN` characters with no spaces count, so descriptions,
    Chinese text and empty placeholders are left out.
    """
    found: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            pattern = _value_pattern(key)
            if pattern is not None:
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and pattern.fullmatch(item.strip()):
                        found.setdefault(item.strip(), str(key))
            if isinstance(value, (dict, list)):
                for secret, source in collect_secrets(value).items():
                    found.setdefault(secret, source)
    elif isinstance(data, list):
        for item in data:
            for secret, source in collect_secrets(item).items():
                found.setdefault(secret, source)
    return found


def config_files(data_dir: Path) -> list[Path]:
    """The framework's main config and every config file next to the plugin configs."""
    return [data_dir / "cmd_config.json", *sorted((data_dir / "config").glob("*.json"))]


def load_config_secrets(paths: Iterable[Path]) -> tuple[dict[str, str], int]:
    """{secret: "file:key"} from the config files that parse, longest secret first; and how many parsed."""
    found: dict[str, str] = {}
    parsed = 0
    for path in paths:
        try:
            if path.stat().st_size > MAX_CONFIG_BYTES:
                continue
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, UnicodeError):
            continue
        parsed += 1
        for secret, key in collect_secrets(data).items():
            found.setdefault(secret, f"{path.name}:{key}")
    ordered = sorted(found.items(), key=lambda item: (-len(item[0]), item[0]))
    return dict(ordered), parsed
