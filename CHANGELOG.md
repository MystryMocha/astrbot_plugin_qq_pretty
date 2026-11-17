# Changelog

Behaviour changes, newest first. Older entries describe the plugin as it was then; the README and the code win where they disagree.

## v1.0.0 First public release

- **Formatting**: model Markdown becomes plain text that QQ shows cleanly.
  Headings become `【】`, list items get `·`, tables become `key：value` lines, and code blocks get a language label with the code left exactly as written.
  By default only model replies on `aiocqhttp` are touched.
- **Outbound check**: every reply, and what other plugins send straight through the OneBot API, is checked before it leaves.
  It looks for private keys, service tokens, JWTs, assigned secrets, server paths and internal addresses, the hosts, paths and names you list, and the keys in AstrBot's own config.
  Modes are `log` (the default), `mask` and `drop`.
- **`/outbound_guard`** (alias `/防泄密`), for admins: the current state, hit counts per rule, and a test that never repeats the text it checks.
- Needs AstrBot `>=4.28.1,<5` and the `aiocqhttp` (OneBot v11) platform.
- MIT License. The notice for the rules adapted from qq-bridge is at the end of `LICENSE` and at the top of `sensitive.py`.

## v0.6.0 Admin command: self-test and hit counts

- **`/outbound_guard`** (alias `/防泄密`), for AstrBot admins only and silent for everyone else (`raise_error=False`). With no argument, or `status` / `状态`, it shows the mode, how many OneBot clients carry the send gate, how many config secrets are protected, how many entries each list has (counts only) and the hit counts.
- **`test <text>`** (`测试`) runs the scan a real send gets (built-in rules, the three lists, known and config secrets) and answers which rules hit and what the current mode would do: send and log, mask N spans, or hold with or without the notice. The text is never repeated, and the log line `qq-pretty: self-test mode= rules= chars=` has rule names only. A test is not counted.
- **Hit counts**: each real hit of the reply check and of the send gate counts once (in `log` mode the gate skips a reply the reply check just logged, as it already did for the log line), per source (`reply` / `send`), per action (`logged` / `masked` / `held`) and per rule, with each rule's last hit time.
  Rule names and numbers only, in `plugin_data/astrbot_plugin_qq_pretty/hit_stats.json`: written at load, 30 s after a hit, when the status is shown and at terminate, so counts and their start time carry over reloads and restarts. A broken file is logged (`hit stats unreadable`) and replaced. `reset` / `清零` starts them again.
- New pure module `selfcheck.py` with `tests/test_selfcheck.py`; command and counting tests in `tests/test_main.py`; the test stub knows `filter.command` and `filter.permission_type`.

## v0.5.0 Send gate on the OneBot client

- **`outbound_check.check_direct_sends`** (on by default): a gate on `api.call_action` of every `aiocqhttp` client checks what plugins send straight through the OneBot API, which the reply check never saw: `send_msg` / `send_group_msg` / `send_private_msg` (text and markdown segments, file names, forward nodes inside; text between the codes of a CQ-code string), the three forward actions (node names and contents, nested forwards, the card's `news` / `prompt` / `summary` / `source`), `upload_group_file` / `upload_private_file` names, `_send_group_notice` and `send_group_ai_record`.
  Streamed replies are sent through the same client piece by piece, so real-time segmenting is covered too. Media `file` values are never read.
- Same rules, lists and mode as the reply check. `mask` sends a rewritten copy and leaves the caller's message untouched; `drop` sends `drop_notice` to the same chat for a plain message and holds a forward or upload silently. A held call returns `None` and never raises, because the framework's send-error line prints the message. A failing check sends the call as it is; protocol errors reach the caller unchanged.
- Installed in `initialize` and on every `on_platform_loaded` (a platform reload builds a new client), removed in `terminate`. The gate is an instance attribute marked with its owner and the method it wraps: a gate left by an earlier load is replaced, not stacked, and one that cannot come off because something wrapped on top of it passes everything through.
- Log line `qq-pretty: send-layer hit action= caller= mode= rules= samples= chars=`, `caller` being the calling plugin's directory or `astrbot`. In `log` mode a reply the reply check just logged is not logged again as the pipeline sends it, and only when the sent text is part of that reply, so a background task that inherits the context still logs its own sends; `mask` and `drop` always log. Startup line `qq-pretty: send gate installed on N OneBot client(s)`.
- New pure module `gate.py` (payload reading, rewriting, install) with `tests/test_gate.py`; `tests/test_main.py` drives the gate on stub clients.

## v0.4.0 Mask mode, AstrBot's own secrets, more token shapes

- **`mask` mode**: each match is replaced with `outbound_check.mask_text` (`[已隐藏]` by default) and the rest of the reply goes out. An assignment hides only its value, a path hides the whole path up to the next non-ASCII character, a PEM key hides through its `-----END` line (or to the end of the text), and a URL with credentials hides only `user:password`. When a secret is cut across two text segments it cannot be masked piece by piece, and the reply is held as in `drop`.
- **`drop_notice`**: when `drop` (or a failed mask) holds a reply, this sentence is sent instead; empty keeps the old silent drop.
- **`protect_config_secrets`** (on by default): `data/cmd_config.json` and every `data/config/*.json` are read at startup and again every 5 minutes, and string values under secret-looking keys (`api_key`, `token`, `secret`, `password`, `jwt_secret`, `ws_reverse_token`, `*_token`, `*_secret`...) become exact-match secrets, held in memory only. A bare `key` / `keys` also needs 16+ characters with letters and digits, so keyword-reply trigger words are left out. Hits log as `config_secret` with `file:key(length)`.
- **Samples**: `known_secret` hits now log only the length, no longer the first 4 characters.
- **New shapes**: `token_prefix` adds GitHub `gho_`/`ghu_`/`ghs_`/`ghr_`, GitLab `glpat-`, Tencent Cloud `AKID`, Alibaba Cloud `LTAI`, Volcengine `AKLT`, Google `ya29.`, Hugging Face `hf_`, Stripe `sk_`/`rk_` live and test keys, xAI `xai-`, Groq `gsk_` and `npm_`; new rules `bot_token` (Telegram, Discord) and `url_credentials` (`scheme://user:password@`). `private_key` also knows `ENCRYPTED PRIVATE KEY`.
- The text segments of a reply are now checked joined with nothing in between, as they are shown, instead of with a newline.
- `tests/test_main.py` drives `check_outbound` on stub events (new `tests/conftest.py` stubs the framework).

## v0.3.0 Site-specific rules move to the config

The display name becomes 「QQ 排版与防泄密」 / QQ Formatter & Outbound Guard.

- **Built-in rules are generic now.** `server_path` keeps `/AstrBot/data`, `/AstrBot/astrbot`, `/data/{astrbot,napcat,llonebot,lagrange,backups}`, `plugin_data/`, `/root/`, `/home/<user>/` and `/etc/{shadow,sudoers}`; `internal_url` keeps astrbot, napcat, llonebot, lagrange, localhost, loopback and private IPs. The hosts, paths and container prefix of one particular deployment are gone from the code.
- **Three literal lists under `outbound_check`**: `internal_hosts` (only as the host after `http(s)://`), `private_paths` (path prefixes, word-bounded) and `internal_names` (whole words anywhere, at least 3 characters). Case-insensitive, no regex; a trailing `*` is a wildcard. Hits keep the `server_path` / `internal_url` rule names; the old `container_name` rule is now `internal_name`, fed only by `internal_names`.
- **Upgrading**: a site that relied on the entries removed from the built-in rules adds them to these lists before updating.
- **Fixes**: every pattern is ASCII-bounded, so a match right next to Chinese text now counts (「配置在/data/astrbot里」 was missed before). A host must end where the name ends, so `https://astrbot.app` is no longer an `internal_url` hit while `http://astrbot:6185` still is.
- Both READMEs get a before / after example that `tests/test_readme_example.py` checks against `convert()`. The qq-bridge MIT notice is now at the top of `sensitive.py`. `metadata.yaml` declares `support_platforms: [aiocqhttp]`.

## v0.2.1 More deployment paths and hosts

`server_path` and `internal_url` cover more paths and hosts of one deployment, and a new `container_name` rule catches a bare container name outside a URL (all moved to the config in v0.3.0).

## v0.2.0 Last outbound gate

Every reply about to be sent (not only model replies but also other plugins' text) is checked once at the very last position of `on_decorating_result` (priority `-sys.maxsize`):

- **Rules** (`sensitive.py`): server paths (`/data/astrbot`, `/data/napcat`, `/AstrBot/data`, `plugin_data/`, `/root/`, `/home/<user>/`, etc.), Windows user directories, internal addresses (container host names, `localhost`, private IPs), credentials with assignments (`api_key = …`, `"token": "…"`, value at least 16 characters), common token prefixes (`sk-`, `ghp_`, `AKIA`, `AIza`, `xox?-`), JWTs, private key headers, and verbatim matches of the "known secrets" entered in the dashboard (things like a bare 32-character hex string that regexes cannot catch).
  Normal chat like 「token 是多少」 ("what's the token") or 「password 忘了」 ("forgot the password") does not count.
- **Mode**: new dashboard item `outbound_check.mode`. `log` (default) only logs; `drop` withholds the whole message on a hit (clears the message chain: the framework sends nothing and `after_message_sent` is not triggered); `off` disables it. The group is never told 「已拦截」 ("blocked").
- **Log**: `qq-pretty: outbound hit mode= rules= samples= llm= chars=`; snippets keep only the first 4 characters and the length. Run in `log` for a while first to review hit samples and confirm false positives before deciding whether to switch to `drop`.
- **Known secrets**: `outbound_check.known_secrets`; entries shorter than 8 characters are ignored. Stored only in the plugin config.
- **Not covered here**: messages other plugins send straight through OneBot (scheduled or follow-up messages, poke replies), and content in non-text segments such as forwards and files (covered from v0.5.0).
- Based on qq-bridge `src/sensitive.js` (MIT, commit `a49877f`). Tests: `tests/test_sensitive.py`.

## v0.1.0 Formatter

Markdown in model replies becomes plain text that reads well in QQ: headings, lists, emphasis, links, tables and code blocks.
