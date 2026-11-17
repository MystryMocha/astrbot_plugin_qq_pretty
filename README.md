**English** · [简体中文](README.zh-CN.md)

# QQ Formatter & Outbound Guard

![Version](https://img.shields.io/badge/version-1.0.0-007AFF)
![License](https://img.shields.io/badge/license-MIT-34C759)
![AstrBot](https://img.shields.io/badge/AstrBot-%E2%89%A54.28.1%20%284.x%29-5856D6)
![Platform](https://img.shields.io/badge/platform-aiocqhttp%20%28OneBot%20v11%29-FF2D55)

**Turns model Markdown into clean QQ text, and checks every outgoing message for server paths, internal addresses and keys.**

QQ over OneBot (NapCat, LLOneBot, Lagrange and the like) does not render Markdown, yet models keep writing `**`, `#` headings, tables and code fences, which show up as raw symbols on a phone. This AstrBot plugin converts them to plain text just before sending. It also checks everything about to go out to QQ: its own replies, and messages other plugins send straight through the OneBot API.

It adds no LLM tools and needs no commands: install it and it works. All settings live in the plugin config.

[Example](#example) · [Why this plugin](#why-this-plugin) · [Quick start](#quick-start) · [Outbound check](#outbound-check) · [Config](#config) · [Limitations](#limitations) · [Changelog](CHANGELOG.md)

> **The outbound check starts in `log` mode**: hits are logged and no message is changed. Review what it catches for a while before switching to `mask` or `drop`; see [Quick start](#quick-start).

---

## Example

![One model reply in QQ without and with the plugin, and a server path and a key masked by the outbound check](https://raw.githubusercontent.com/MystryMocha/astrbot_plugin_qq_pretty/main/assets/preview.en.png)

What QQ shows without the plugin:

<!-- readme-example:input -->
````markdown
### Option comparison

| Option | Cost | Speed |
|---|---|---|
| Local | Low | Slow |
| Cloud API | Pay as you go | Fast |

**Verdict**: start with the cloud API, move local later.

- Pros
  - Quick to set up, no GPU
- Cons: billed per use

```python
print("hello")
```
````

What the chat receives:

<!-- readme-example:output -->
```text
【Option comparison】

Option：Local · Cost：Low · Speed：Slow
Option：Cloud API · Cost：Pay as you go · Speed：Fast

Verdict: start with the cloud API, move local later.
· Pros
  · Quick to set up, no GPU
· Cons: billed per use

【python】
print("hello")
```

---

## Why this plugin?

- **Readable on every reply** — Headings become `【】`, list items get `·`, tables become `key：value` lines and code blocks get a language label, with the code itself left exactly as written. The conversion runs after the model has answered, so it works whether or not the model follows your prompt.
- **Leaves other plugins' text alone** — By default only model replies are formatted. Text that other plugins format themselves, such as fortunes, check-ins and daily reports, goes out unchanged. Text without Markdown is never touched, and if the conversion fails, the original text is sent.
- **A final check before anything leaves** — Each reply is checked at the end of the reply pipeline, and a gate on the OneBot send API covers what other plugins send directly: scheduled posts, forwards, uploaded file names and streamed segments.
- **Protects AstrBot's own keys automatically** — API keys, tokens and passwords already in AstrBot's config are covered without entering them again. Add your own hosts, paths and container names as plain text, with `*` wildcards and no regex.
- **Observe before you enforce** — It starts in log-only mode. `/outbound_guard` shows how often each rule has hit and can test a piece of text without echoing it, so you can rule out false positives before anything is masked or held. Logs never contain a whole secret: only masked samples (the first 4 characters), lengths and key names.

### How it compares

| | Raw model output | "No Markdown" in the persona prompt | **This plugin** |
|---|---|---|---|
| **Headings, lists, bold** | `#`, `-` and `**` show as symbols | Depends on the model following it every time | **Converted on every reply, after generation** |
| **Tables** | Rows of pipes and dashes | Depends on the model rewriting them | **One `key：value` line per row** |
| **Code** | Sent with the fences | Depends on the model | **Language label, code kept exactly** |
| **Sensitive data in a reply** (server paths, internal URLs, keys) | Sent unchanged | Sent unchanged | **Logged, masked or held** |
| **Sensitive data in other plugins' direct sends** | Sent unchanged | Sent unchanged | **Checked at the OneBot send API** |

---

## Features

| Area | What it does |
|---|---|
| **Formatting** | Converts headings, nested lists, task lists, tables, code blocks, links, quotes and common HTML tags to plain text. Only `Plain` segments are touched. See [Formatting](#formatting). |
| **Reply check** | Runs last in the reply pipeline, on every reply to the listed platforms, whether the model wrote it or not. |
| **Send gate** | Wraps each `aiocqhttp` client's `api.call_action` and checks messages, forwards, file uploads, group notices and AI voice text. See [Direct sends](#direct-sends). |
| **Built-in rules** | Private keys, service tokens (`sk-`, GitHub, AWS, Tencent Cloud, Alibaba Cloud and more), bot tokens, JWTs, `key = value` assignments, URLs with passwords, server paths and internal URLs. See [Rules](#rules). |
| **Config secrets** | Reads AstrBot's main config and every file under `data/config/`, and protects the values under secret-looking keys. Re-read every 5 minutes. |
| **Custom lists** | `internal_hosts`, `private_paths`, `internal_names` and `known_secrets`, all matched literally. |
| **Modes** | `log` (default), `mask`, `drop` or `off`, shared by both check points. |
| **Admin command** | `/outbound_guard` (`/防泄密`): status, hit counts per rule, a test that never echoes the text, and a reset. See [Admin command](#admin-command). |

---

## Quick start

Requires AstrBot 4.28.1 or later (4.x), with QQ connected through the `aiocqhttp` (OneBot v11) adapter.

| Option | How |
|---|---|
| **1. From the repository (recommended)** | In the AstrBot dashboard, install a plugin from the URL `https://github.com/MystryMocha/astrbot_plugin_qq_pretty` |
| **2. From a zip** | Download this repository as a zip and upload it in the AstrBot dashboard |

The plugin directory must be named `astrbot_plugin_qq_pretty`. Both methods use this name; the plugin relies on it to find its data folder and to recognise its own calls.

Then check it in 3 steps:

1. **Check the formatting**: Ask the bot something the model will answer with a list or a table. The chat should receive plain text; nothing needs configuring.
2. **Check the guard**: As an AstrBot admin, send `/outbound_guard` (or `/防泄密`). The reply shows the mode, how many OneBot clients have the send gate installed and how many config secrets are protected.
3. **Choose a mode after observing**: Run in `log` mode for a while. Use `/outbound_guard` to see which rules hit, check the matching log lines for false positives, then set `outbound_check.mode` to `mask` or `drop`.

---

## Formatting

Only `Plain` segments are touched; images and other components pass through unchanged. Text without Markdown is left exactly as it was, and if the conversion fails, the original text is sent. By default only model replies are changed (`llm_only`), so text other plugins have already formatted is left alone. Streamed replies are not formatted; see [Limitations](#limitations).

| Markdown | Becomes |
|---|---|
| `# Heading` (levels 1–6) | `【Heading】`, or just the text with `heading_style: plain` |
| `- item`, `* item`, `+ item` | `· item`, indented two spaces per level |
| `- [x] done`, `- [ ] todo` | `✓ done`, `□ todo` |
| Two-column table with a header | One `key：value` line per row |
| Wider table with a header | One line per row: `Header：value · Header：value` |
| Table that starts with its `---` row (no header) | Cells joined by ` · ` |
| Code fence | `【python】` on the first line, then the code (`【代码】` without a language); see `code_block_style` |
| Inline `` `code` `` | Backticks removed (kept when `keep_inline_code` is on) |
| `**bold**`, `*italic*`, `~~strike~~` | Markers removed |
| `[label](url)`, `![alt](url)` | `label url`, `alt url` (just the url when the label is empty or the same as the url) |
| `> quote` | `｜quote` |
| `---` rules | Removed |
| `<br>` | Line break |
| `<b>`, `<i>`, `<em>`, `<strong>`, `<p>`, `<div>`, `<span>`, `<h1>`–`<h6>` | Removed |

Ordered lists are left as they are. Code blocks and inline code are set aside first, so nothing inside them is rewritten. Three or more consecutive newlines collapse into one blank line. The output uses the full-width punctuation common in Chinese chats (`：`, `【】`, `｜`); `【代码】` means "code".

---

## Outbound check

The reply check runs at the very last `on_decorating_result` position (priority `-sys.maxsize`), so it sees the final text. It covers every outgoing reply on the `platforms` list, model-generated or not; it shares `platforms` with formatting but ignores `enabled` and `llm_only`. Messages sent any other way go through the [send gate](#direct-sends) below. Both check points use the same rules, lists and mode.

### Rules

Built-in rules (in `sensitive.py`); each rule counts at most once per message:

| Rule | Matches |
|---|---|
| `known_secret` | A verbatim entry from `known_secrets` |
| `config_secret` | A secret from AstrBot's config files, verbatim; see [Secrets in AstrBot's config](#secrets-in-astrbots-config) |
| `private_key` | PEM private key headers |
| `token_prefix` | Service tokens of realistic length: `sk-` (OpenAI, Anthropic and the many compatible APIs), GitHub tokens such as `ghp_`, `glpat-`, `AKIA` (AWS), `AKID` (Tencent Cloud), `LTAI` (Alibaba Cloud), `AKLT` (Volcengine), `AIza`, `ya29.`, `xox?-`, `hf_`, `sk_live_` / `rk_live_`, `xai-`, `gsk_`, `npm_` |
| `bot_token` | Telegram and Discord bot tokens |
| `jwt` | Three-part `eyJ…` tokens |
| `assigned_secret` | `api_key = …`, `"token": "…"` and the like, with a value of at least 16 characters |
| `url_credentials` | A URL carrying a user and password, such as `redis://user:password@host` or `mysql://…`, any scheme |
| `server_path` | `/AstrBot/data`, `/AstrBot/astrbot`, `/data/{astrbot,napcat,llonebot,lagrange,backups}`, `plugin_data/`, `/root/`, `/home/<user>/`, `/etc/shadow`, `/etc/sudoers`, plus `private_paths` |
| `windows_user_path` | Windows user home paths |
| `internal_url` | `http(s)://` to astrbot, napcat, llonebot, lagrange, localhost, loopback and private IPs, plus `internal_hosts` |
| `internal_name` | A name from `internal_names` appearing as a whole word |

Everyday chat such as 「token 是多少」 ("what's the token") or 「password 忘了」 ("forgot the password") does not count, nor do public URLs like `https://astrbot.app`, relative paths like `./data/…`, or the same path inside a public URL. Rules still match when the text runs straight into Chinese characters, as in 「配置在/data/astrbot里」.

### Adding your own hosts, paths and names

The built-in rules only cover common deployments. Put your own container names, service names and directories in the three lists below. Entries are matched literally and case-insensitively, with no regex to write; a trailing `*` is a wildcard.

| Key | When it matches | Example |
|---|---|---|
| `internal_hosts` | Only as the host after `http(s)://`; the bare word in text does not count | `my-proxy` matches `http://my-proxy:8080`, not `https://my-proxy.example.com` |
| `private_paths` | A path starting with it; `/data/my-bot` does not match `/data/my-botx` | `/data/my-bot`, `/opt/stack-*` |
| `internal_names` | Anywhere in the text, as a whole word; entries under 3 characters are ignored | `mybot-prod-*` matches `mybot-prod-astrbot` |

Because `internal_names` matches anywhere in the text, use it only for distinctive names such as container names, never for common words.

### Secrets in AstrBot's config

No rule can catch a bare 32-character hex key, but AstrBot knows which keys it has been given. `protect_config_secrets` (on by default) reads the main config `data/cmd_config.json` and every config file under `data/config/` (plugin configs and config profiles) when the plugin starts and every 5 minutes after that. Values under secret-looking keys are treated as known secrets:

- the key is `api_key`, `token`, `secret`, `password`, `access_token`, `jwt_secret`, `ws_reverse_token` and the like, or ends in `_token`, `_secret`, `_api_key`, `_password` and so on;
- the value is at least 8 characters of printable ASCII with no spaces;
- under a bare `key` / `keys` (the name of the provider API key list, though a keyword-reply plugin may store trigger words there too), the value must also be at least 16 characters and contain both letters and digits.

The collected secrets are kept in memory only. Logs show only their count, and a hit shows `file:key(length)`, never the value. For secrets that are not in AstrBot's config, use `known_secrets`.

### Mode

`outbound_check.mode`:

- `log` (default): only logs.
- `mask`: replaces each match with `mask_text` (`[已隐藏]`, "hidden", by default) and sends the rest. An assignment hides only its value (`api_key = [已隐藏]`), a path hides the whole path, a private key is hidden through its end line, and a URL with credentials hides only the user and password. If a message is split into several text segments and a secret spans two of them, it cannot be masked segment by segment, so the reply is handled as in `drop`.
- `drop`: sends nothing. With `drop_notice` set, only that notice is sent; without it the message chain is cleared, so the framework sends nothing, `after_message_sent` does not fire and the chat gets no notice.
- `off`: no check.

Any other value is treated as `log`.

Log line: `qq-pretty: outbound hit mode= rules= samples= llm= chars=`. Samples keep only the first 4 characters and the length; `known_secret` shows only the length, `config_secret` the source file, key name and length.

### Direct sends

Plugins can send to QQ without going through the reply pipeline, for example scheduled posts, replies to pokes in a group, forwards and file uploads. The framework also sends streamed replies piece by piece. To cover these, `check_direct_sends` (on by default) installs a gate on the OneBot client itself: the `api.call_action` of every `aiocqhttp` platform. The gate is installed when the plugin loads and whenever a platform reloads, and removed when the plugin unloads. It is installed only when `aiocqhttp` is on the `platforms` list.

Text checked for each OneBot action:

| Action | Text checked |
|---|---|
| `send_msg`, `send_group_msg`, `send_private_msg` | Text and markdown segments, file names and forward nodes inside the message; for a CQ-code string, the text between the codes |
| `send_forward_msg`, `send_group_forward_msg`, `send_private_forward_msg` | Each node's name and content, including nested forwards, and the card's `news`, `prompt`, `summary` and `source` |
| `upload_group_file`, `upload_private_file` | The file name |
| `_send_group_notice`, `send_group_ai_record` | The notice text; the text to be spoken |

Other actions pass straight through. Media `file` values (base64 images, paths, URLs) are never read.

The modes apply here too:

- `log`: the call goes out unchanged.
- `mask`: a copy with the matches hidden is sent; the caller's own message list is not modified.
- `drop`: nothing is sent. For a plain message with `drop_notice` set, the notice goes to the same chat instead; forwards and uploads are held silently.

A held call returns `None` instead of raising an error, because the framework's send-error log line would print the held message. If the check itself fails, the call goes out unchanged and the error is logged. Errors from the protocol side, such as being muted, reach the caller unchanged. To the plugin that made the call, a held message looks sent: it is not retried or sent another way.

Log line: `qq-pretty: send-layer hit action= caller= mode= rules= samples= chars=`, where `caller` is the plugin directory whose code made the call, or `astrbot` when the framework sends a reply itself. In `log` mode, a reply already logged by the reply check is not logged again when it is sent.

---

## Admin command

`/outbound_guard` (alias `/防泄密`) is for AstrBot admins only. From anyone else it is treated as ordinary chat and gets no answer. Replies are in Simplified Chinese.

| Command | What it does |
|---|---|
| `/outbound_guard` or `/outbound_guard status` | Shows the mode, how many OneBot clients have the send gate installed, how many AstrBot config secrets are protected, how many entries each list has (counts only, never the entries), and the hit counts |
| `/outbound_guard test <text>` | Checks the text against the same rules, lists and secrets as a real send, and replies with the rules it hits and what the current mode would do (send and log, mask a number of spans, or hold the whole message). The text itself is never repeated |
| `/outbound_guard reset` | Resets the hit counts |

The sub-commands also accept `状态`, `测试` and `清零`.

**Hit counts**: every real hit of the reply check and of the send gate is counted once; a reply caught at both points counts once. Counts are kept per source (reply, direct send), per action (logged, masked, held) and per rule, along with each rule's last hit time. Only rule names and numbers are stored, in `plugin_data/astrbot_plugin_qq_pretty/hit_stats.json`. The file is written within 30 seconds of a hit, when the status is shown and when the plugin unloads, so counts carry over reloads and restarts. A corrupted file is replaced and counting starts again. Masked samples stay in the log only; look for `qq-pretty: outbound hit` and `send-layer hit` around the time shown. `off` mode checks nothing and counts nothing; `test` is never counted.

Text sent to `test` is visible to other plugins like any other message (chat history, memory, group summaries) and may reach a model. Test with fake values of the same shape, never a real secret, and preferably in a private chat with the bot. To check that the secrets in AstrBot's config are covered, read their count in the status; there is no need to paste one. A test writes only the log line `qq-pretty: self-test mode= rules= chars=`, with no samples.

---

## Config

| Key | Default | Meaning |
|---|---|---|
| `enabled` | on | Turns formatting on or off. When off, text is sent as written, including `**`, `#` and tables |
| `llm_only` | on | Format model replies only. When off, all outgoing plain text is formatted |
| `platforms` | `aiocqhttp` | Platforms for both formatting and the check. Do not add `webchat`: the WebUI renders Markdown itself. An empty list falls back to the default |
| `heading_style` | `bracket` | `bracket` or `plain` |
| `code_block_style` | `label` | `label` (`【语言】`, "language", on the first line, then the code; easy to copy), `indent` (each line indented two spaces) or `keep` (fences kept) |
| `keep_inline_code` | off | Keep inline backticks |
| `outbound_check.mode` | `log` | `off`, `log`, `mask` or `drop` |
| `outbound_check.mask_text` | `[已隐藏]` | Replacement text in `mask` mode |
| `outbound_check.drop_notice` | empty | Notice to send instead when `drop` holds a reply; leave empty to send nothing |
| `outbound_check.check_direct_sends` | on | Also check at the OneBot send API; see [Direct sends](#direct-sends) |
| `outbound_check.protect_config_secrets` | on | Treat the secrets in AstrBot's config as known secrets; see [Secrets in AstrBot's config](#secrets-in-astrbots-config) |
| `outbound_check.known_secrets` | empty | Strings to match verbatim that are not in the config files; entries under 8 characters are ignored |
| `outbound_check.internal_hosts` | empty | Internal host names; see [Adding your own hosts, paths and names](#adding-your-own-hosts-paths-and-names) |
| `outbound_check.private_paths` | empty | Private path prefixes; see the same section |
| `outbound_check.internal_names` | empty | Internal names; see the same section |

These lists are stored only in the plugin config, and logs show only rule names and masked samples.

### Switching off

Turn off `enabled` to stop formatting. Set `outbound_check.mode` to `off` to stop the check; the two are independent. Turn off `outbound_check.check_direct_sends` to keep only the reply check and leave the OneBot client untouched.

---

## Limitations

- **Streamed replies are not formatted**: With streaming output on, the framework skips result decoration, so streamed replies go out unformatted; the send gate still checks them. To keep formatting, set the framework's "platforms without streaming support" option to turn streaming off.
- **Text only**: Images, voice and file contents are not checked, and media `file` values are never read.
- **Rule-based, not a guarantee**: The rules recognise the usual shapes of paths, addresses and keys, plus the exact values from AstrBot's config and your lists. A secret in an unusual format, rewritten by the model, or split across two streamed segments or two messages can get through.
- **Held sends look successful to the caller**: The plugin that made the call is not told; it does not retry or send another way. See [Direct sends](#direct-sends).
- **Traces keep the unmasked text (AstrBot 4.29.4+)**: AstrBot can keep a trace of what it does (`trace_enable`, off by default; stored in `data/logs/traces.db` unless configured otherwise). From AstrBot 4.29.4, an enabled trace also records the text of `event.send` calls and of streamed replies (about the first 4 KB of a stream). It records the message as the plugin handed it over, not the copy the send gate masked, so that text stays unmasked in the trace. Replies in the reply pipeline are masked earlier, at the decorating stage. The trace stays on the machine; none of it is sent to QQ.
- **Chinese output**: Admin command replies, the default `mask_text` and the code label are in Simplified Chinese. `mask_text` and `drop_notice` accept any text.
- **Only run on AstrBot 4.28.1**: AstrBot 4.29.4 and 4.30.0-alpha.1 were checked by reading their source, not by running the plugin on them.

---

## Development

Run the tests from the plugin directory. Lightweight AstrBot stand-ins in `tests/conftest.py` let them run without the framework:

```bash
python -m pytest tests -q
```

| Path | Contents |
|---|---|
| `main.py` | Plugin entry: config, the reply check, send gate installation, the admin command |
| `convert.py` | Markdown to plain text |
| `sensitive.py` | Outbound check rules and config secret collection |
| `gate.py` | Which text the send gate reads and rewrites for each OneBot action |
| `selfcheck.py` | Admin command wording, replies and hit counts |
| `tests/` | The test suite, including a check that the [example](#example) above matches the formatter's actual output |
| `assets/` | The preview images, and `make_preview.py`, which renders them from the example and the plugin's own output (needs Pillow and Edge or Chrome) |

## Contributing and support

Bug reports and suggestions are welcome in Issues. If some text should have been caught, or was caught by mistake, describe its shape with a made-up value; never paste a real secret into an issue.

If the plugin keeps your group chats tidy, a **Star** on GitHub helps other AstrBot users find it.

Version history: [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE). The outbound check rules are adapted from [qq-bridge](https://github.com/Derpyu520/qq-bridge) (MIT, commit `a49877f`); its copyright and permission notice is included at the end of [LICENSE](LICENSE) and at the top of `sensitive.py`.

QQ is a trademark of Tencent. This project is not affiliated with Tencent or with the AstrBot project.
