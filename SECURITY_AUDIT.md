# Security Audit — Salt & Soil

| | |
|---|---|
| **Audit date** | 2026-11-16 |
| **Audited revision** | `3c61269` (v1.1.3) |
| **Scope** | Everything in the repo: `src/`, `scripts/`, `config/`, templates, dependencies, docs |
| **Method** | Manual code review · dependency scan (`pip-audit`) · proof-of-concept runs against a local orchestrator and a hostile test agent (NFS mounts stubbed) · git-history secret scan |
| **Result** | 19 items — 1 High · 6 Medium · 10 Low · 2 Info |

## How to read this file

**Threat model.** The README defines Salt & Soil as a single-operator tool on a trusted LAN + Tailscale. Severity is rated *inside* that model. The attackers considered are: another device or user on the LAN / tailnet, a compromised or spoofed agent, a web page opened in the operator's browser, and a local non-root user on the host.

**Severity.** `High` = exploitable in the default setup with real impact · `Medium` = needs a precondition (network position, compromised peer, timing) but the impact is serious · `Low` = hardening / limited impact · `Info` = hygiene.

**Status.** `Open` · `In progress` · `Fixed (<commit>)` · `Accepted` (risk consciously kept) · `Won't fix`. Update the overview table and the item's own status line together.

**Known** marks items the README already acknowledges under *Scope and trust model*. They are listed anyway so that everything is tracked in one place.

**Verified** says how the finding was confirmed: `PoC` = reproduced against running code, `Code` = established by reading the code, `Scan` = tool output, `Docs` = based on upstream documentation and not reproduced here.

## Overview

| ID | Sev | Item | Solution (short) | Status |
|----|-----|------|------------------|--------|
| [SEC-01](#sec-01) | High | Agent API is unauthenticated unless a key is configured (fail-open default) | Fail closed: refuse to start without `auth.api_key`; generate one in bootstrap; constant-time compare | Open |
| [SEC-02](#sec-02) | Medium | XSS in the error banner — agent-controlled text is rendered as HTML | Escape in `showBanner()` / use `textContent` | Fixed |
| [SEC-03](#sec-03) | Medium | Folder names and mount points from the agent are trusted → path traversal, `rm -rf` of a whole source | Validate names as single path components; containment check before rsync / rm | Fixed |
| [SEC-04](#sec-04) | Medium | SSH host-key verification disabled (`StrictHostKeyChecking=no`) | `accept-new` + dedicated `known_hosts`; `BatchMode`, `IdentitiesOnly` | Open |
| [SEC-05](#sec-05) | Medium | No transport security: UI and agent API over plain HTTP, cookie not `Secure` | TLS option / reverse proxy, `cookie_secure` setting, `https://` agents | Open |
| [SEC-06](#sec-06) | Medium | First-run `/setup` is open to whoever reaches it first (and reopens if `auth.toml` is missing) | One-time setup token from the journal, or a CLI `create-user` | Fixed |
| [SEC-07](#sec-07) | Medium | Pinned dependencies with known CVEs (pre-auth DoS in form parsing) | Bump `fastapi`/`starlette`, `python-multipart`, `jinja2`, `pytest`; add `pip-audit` | Fixed |
| [SEC-08](#sec-08) | Low | Login throttle is global (anyone can lock the operator out) + username timing oracle | Per-IP throttle with backoff; dummy hash verify for unknown users | Fixed |
| [SEC-09](#sec-09) | Low | Sessions cannot be revoked; valid 30 days regardless of "Remember me"; logout via GET | Enforce 24 h for non-remember tokens; rotate secret on logout; drop `GET /logout` | Fixed |
| [SEC-10](#sec-10) | Low | CSRF defence is `SameSite=Lax` only; JSON endpoints accept any content type | `Origin` / `Sec-Fetch-Site` check in the auth middleware; `SameSite=Strict`; require JSON | Fixed |
| [SEC-11](#sec-11) | Low | `/api/reset` unmounts during a running sync; check-then-act races; actions not validated server-side | Remove or guard `/api/reset`; lock state transitions; action whitelist per diff status | Fixed |
| [SEC-12](#sec-12) | Low | `data/auth.toml` is world-readable (0644) and written non-atomically | Write 0600 + atomic replace; `data/` 0700; `UMask=0077` | Fixed |
| [SEC-13](#sec-13) | Low | No security headers (CSP, framing, nosniff), inline JS, third-party fonts, no Host validation | Header middleware; move JS to `/static`; self-host fonts; optional trusted-host list | Open |
| [SEC-14](#sec-14) | Low | Orchestrator key is an unrestricted root shell on the agent; README leaves password SSH login on | Restrict the key (`rrsync`), move delete to an agent endpoint, tighten sshd docs | Open |
| [SEC-15](#sec-15) | Low | rsync / NFS hardening: no `--protect-args`; mounts allow suid/dev/exec | Add `-s`; add `nosuid,nodev,noexec` to default `nfs_options` | Open |
| [SEC-16](#sec-16) | Low | Runs as root with no service sandboxing; binds `0.0.0.0`; `test-mount` serves an unauthenticated UI on all interfaces | Bind narrowly; harden the systemd unit; `test-mount` → `127.0.0.1` | Open |
| [SEC-17](#sec-17) | Low | Scheduled runs push changes unattended, while the README says sync is never automatic | Add a `schedule.auto_sync` switch (default off) or correct the docs | Open |
| [SEC-18](#sec-18) | Info | Config validation gaps: absolute `local_path` / `remote_path` bypass the `..` check | Reject absolute paths; restrict alias charset | Fixed |
| [SEC-19](#sec-19) | Info | Supply chain: `curl \| tar`, `curl \| sh`, no hashes, unpinned transitive deps | Publish checksums; hash-pinned lock file | Open |

---

## Findings

<a id="sec-01"></a>
### SEC-01 — Agent API is unauthenticated unless a key is configured · High

- **Where:** `src/salt_and_soil/transport/api_server.py:475-485`, `config/config.example.toml:20,53`, `README.md:76`
- **Status:** Open · **Known** (README) · **Verified:** PoC
- **What:** `require_api_key` returns early when `auth.api_key` is empty, and the example config ships with an empty key. The agent then only logs a warning. It also binds `0.0.0.0`, so the API is reachable from the agent's whole LAN, not just the tailnet. `/health`, `/docs` and `/openapi.json` are public even when a key is set, and the "unknown alias" error lists all configured aliases. The key comparison uses `!=` (not constant-time).
- **Impact:** Any device on the agent's LAN or tailnet can list folder names and sizes, wake the NAS (`/mount`), and unmount shares in the middle of a run (`POST /unmount` with no body unmounts everything; later transfers in that run then fail or — for sources at the share root — land in the bare mount-point directory on the agent's own disk). Because the endpoints need no credentials and accept bodiless POSTs, a web page opened in a browser on that network can trigger them blindly as well.
- **Solution:**
  1. Fail closed — refuse to start in agent role when `auth.api_key` is empty, unless an explicit `auth.allow_unauthenticated = true` is set.
  2. Let `bootstrap.sh --role agent` generate a key (`secrets.token_urlsafe(32)`) and print it for the orchestrator config; make the orchestrator refuse an `[[agents]]` entry without `api_key`.
  3. Compare with `hmac.compare_digest`.
  4. Create the agent app with `docs_url=None, redoc_url=None, openapi_url=None`; drop the alias list from the error message.
  5. Update the README trust-model paragraph (it still says the API is always unauthenticated).

<a id="sec-02"></a>
### SEC-02 — XSS in the error banner · Medium

- **Where:** `src/salt_and_soil/templates/index.html:1158-1164` (`showBanner`), called at `:746`, `:1129`, `:1206`; sources: `transport/api_client.py:56-59,97-103`, `roles/orchestrator.py:261`
- **Status:** Fixed · **Verified:** PoC (script executed in Chromium in the logged-in session)
- **What:** `showBanner()` inserts `msg` with `innerHTML`. The message is the runtime's `error` string, which can contain text the agent controls: the `detail` of any agent HTTP error and the agent's mount error output. Everything else in the UI is escaped (`esc()`, `textContent`); this is the one sink that is not.
- **Impact:** A compromised agent — or anyone who can tamper with the plain-HTTP link to it (SEC-05) — gets JavaScript running in the operator's authenticated session. That script can drive the whole API: start scans, change the schedule, execute sync and delete actions. The password cannot be changed this way (the current password is required).
- **Solution:** Escape in `showBanner()` (`${esc(msg)}`) or build the banner with `textContent`. One-line fix. A Content-Security-Policy (SEC-13) is the second line of defence.
- **Fix (2026-11-16):** `showBanner()` now escapes its message with `esc()`. Re-ran the hostile-agent test in Chromium: the payload is displayed as text and no longer executes. The CSP second line of defence remains open under SEC-13.

<a id="sec-03"></a>
### SEC-03 — Folder names and mount points from the agent are trusted · Medium

- **Where:** `transport/dtos.py:33-55`, `roles/orchestrator.py:270-277,338-348`, `sync/executor.py:100-104,176-186`
- **Status:** Fixed · **Verified:** PoC
- **What:** `/list` entries become diff rows without validation, and `/status` mount points are used as the base of remote paths. Names are later joined into rsync and `rm -rf` paths. In the PoC a test agent returned the names `""`, `".."` and `"../../../../../root/.ssh"`; all three were accepted as `remote_only` rows and produced:
  - `rm -rf <remote source>/` — the whole source (empty name)
  - `rm -rf <remote source>/..` — the whole share
  - an rsync **pull** whose local destination resolves outside the mount, written as root
- **Impact:** Needs a hostile/spoofed agent *and* the operator choosing Pull/Delete on those rows — but the "Mirror to remote" bulk action selects Delete for every `remote_only` row, and the empty-name row is practically invisible in the table. The good news: actions posted to `/api/execute` are only matched against server-side diffs, so the browser cannot inject paths; local names come from `iterdir()` and are safe.
- **Solution:**
  1. Validate every name from `/list`: non-empty, not `.` or `..`, no `/`, no NUL or control characters; `size_bytes` a non-negative int; ignore unknown keys instead of `DirEntry(**e)`.
  2. Validate `/status` mount points: absolute, normalised, inside `mount_root_remote`.
  3. Defence in depth in `SyncExecutor`: before rsync or delete, check that `normpath(join(base, folder))` is strictly inside `base`; use `rm -rf --`.
  4. Add tests for all three cases.
- **Fix (2026-11-16):** `/list` responses are validated on arrival (`DirEntry.from_dict`): a folder name must be a single path component — non-empty, not `.` or `..`, no `/`, no NUL — and `size_bytes` a non-negative integer; unknown keys are ignored. A bad entry fails the scan with a readable error instead of becoming a row. `/status` mount points must lie below `mount_root_remote` and are only accepted for sources that belong to the reporting agent. `SyncExecutor` re-checks the folder name before every rsync and delete, and delete uses `rm -rf --`. Re-ran the hostile-agent test: the scan is refused and no command is built. Tests: `tests/test_agent_input_validation.py`. **Deviation:** other control characters are not rejected — they are legal in real folder names and the local scanner allows them too. **Behaviour change:** if an agent uses a mount root different from the orchestrator's `mount_root_remote`, scans now stop with an error that says which setting to align.

<a id="sec-04"></a>
### SEC-04 — SSH host-key verification disabled · Medium

- **Where:** `sync/executor.py:56-62` (rsync transport), `:181-186` (remote delete)
- **Status:** Open · **Verified:** Code
- **What:** Both SSH invocations pass `-o StrictHostKeyChecking=no`, so the agent's identity is never checked.
- **Impact:** Whoever can redirect the connection (LAN position, DNS/ARP, or a non-Tailscale deployment such as the `10.0.0.2` in the example config) can impersonate the agent: receive everything that is pushed, feed arbitrary data on a pull, and swallow deletes. The private key itself is not exposed and cannot be replayed against the real agent. Over Tailscale the WireGuard layer already authenticates the peer, which is why this is not High.
- **Solution:** Use `StrictHostKeyChecking=accept-new` with a dedicated `UserKnownHostsFile` (e.g. `<data_dir>/known_hosts`), plus `BatchMode=yes` and `IdentitiesOnly=yes`. Document the one-time `ssh-keyscan` / first-connect step and how to reset the entry when an agent is rebuilt.

<a id="sec-05"></a>
### SEC-05 — No transport security · Medium

- **Where:** `transport/api_server.py:185-197` (`secure=False`), `roles/orchestrator.py:60` (`http://` agent URL), `cli.py:49`
- **Status:** Open · **Known** (README) · **Verified:** Code
- **What:** The web UI is HTTP-only, the session cookie is hard-coded without `Secure`, and the orchestrator talks to the agent over plain HTTP with the API key in a header.
- **Impact:** On any network segment that is not Tailscale, the login password, the session cookie and the agent API key can be sniffed, and agent responses can be modified in transit — which is what turns SEC-02 and SEC-03 from "compromised agent" into "anyone on the path".
- **Solution:**
  1. Add `[server] cookie_secure` (or derive it from the request scheme / `X-Forwarded-Proto` behind a trusted proxy) instead of the hard-coded `False`.
  2. Document a TLS front (`tailscale serve`, Caddy, nginx) for the UI and optionally expose uvicorn's `ssl_certfile` / `ssl_keyfile`.
  3. Allow `[[agents]] url = "https://…"` and recommend binding the agent to its Tailscale address (SEC-16).

<a id="sec-06"></a>
### SEC-06 — First-run `/setup` is open to whoever reaches it first · Medium

- **Where:** `transport/api_server.py:200-235`
- **Status:** Fixed · **Verified:** PoC
- **What:** Until `data/auth.toml` exists, anyone who can reach the port can create the single account and is logged in immediately. The check is "file exists", so the window reopens whenever that file is missing — a fresh install started by `install-service.sh`, a restore without `data/`, a moved `data_dir`.
- **Impact:** Full control of the orchestrator (sync and delete actions on both NAS devices) for whoever wins the race; with a lost `auth.toml` there is no race at all.
- **Solution:** Generate a one-time setup token at startup when no account exists, log it to the journal, and require it on the setup form. Alternatively create the account from the CLI (`python -m salt_and_soil create-user`) and remove the web setup. Cheap extra: only accept `/setup` from loopback unless a token is presented.
- **Fix (2026-11-16):** Creating the account now requires a one-time setup token (`auth/setup_token.py`). It is generated in memory whenever the app finds no account — at startup, or when `/setup` is opened after `auth.toml` was removed — written to the service log, and discarded once the account exists. The setup form has a token field with a hint where to find it (`journalctl -u salt-and-soil | grep token`); the comparison is constant-time and ignores case, dashes and spaces so the token can be typed by hand. Verified end to end in Chromium, and with the real `serve` command that the token reaches the log exactly once. The auth pages now scroll on short screens instead of clipping the longer form. Tests: `tests/test_setup_token.py`. Existing installs are unaffected: no token is issued while an account exists.

<a id="sec-07"></a>
### SEC-07 — Pinned dependencies with known CVEs · Medium

- **Where:** `requirements.txt`
- **Status:** Fixed · **Verified:** Scan (`pip-audit`, 2026-11-16)

| Package (pinned) | Advisories | Relevant here? | Fixed in |
|---|---|---|---|
| `python-multipart` 0.0.12 | CVE-2024-53981, CVE-2026-24486, CVE-2026-40347, CVE-2026-42561, CVE-2026-53538, CVE-2026-53539, CVE-2026-53540 | **Yes** — parses the `/login` and `/setup` forms *before* authentication (DoS, parser differentials). The upload-path CVE does not apply (no uploads). | ≥ 0.0.31 |
| `starlette` 0.38.6 (via `fastapi` 0.115.0) | CVE-2024-47874, CVE-2025-54121, CVE-2026-48710, CVE-2026-48817, CVE-2026-48818, CVE-2026-54282, CVE-2026-54283 | **Yes** — form-parsing DoS reachable before authentication (CVE-2024-47874, CVE-2025-54121, CVE-2026-54283). The others (URL reconstruction, Windows `StaticFiles`, `HTTPEndpoint` dispatch) do not appear reachable in this app. | ≥ 1.3.1 |
| `jinja2` 3.1.4 | CVE-2024-56201, CVE-2024-56326, CVE-2025-27516 | No — all require attacker-controlled templates; templates here are static. | ≥ 3.1.6 |
| `pytest` 8.3.2 | CVE-2025-71176 | Dev only. | ≥ 9.0.3 |

- **Solution:**
  1. Bump `fastapi` to a release that allows Starlette ≥ 1.3.1, `python-multipart` ≥ 0.0.31, `jinja2` ≥ 3.1.6. The templates are called as `TemplateResponse(name, {"request": …})` (`api_server.py:204,228,244,267,282,338`); newer Starlette expects `TemplateResponse(request, name, context)`, so adjust those calls in the same change.
  2. Move `pytest` / `pytest-asyncio` to a `requirements-dev.txt`.
  3. Run `pip-audit` in CI (or enable Dependabot) so this item does not silently reopen.
- **Fix (2026-11-16):** Pinned `fastapi` 0.162.5, `starlette` 1.7.0 (now pinned explicitly), `python-multipart` 0.0.32 and `jinja2` 3.1.6 — the newest releases that still support Python 3.10. `pip-audit` reports no known vulnerabilities for the fully resolved set (38 packages). Template calls use the request-first signature (the old form raises on Starlette 1.x) and are covered by page smoke tests in `tests/test_web_pages.py`. `pytest` 9.2.0 / `pytest-asyncio` 1.5.0 moved to `requirements-dev.txt`. Tested on Python 3.11 and 3.13. **Not done:** automated `pip-audit` — the repo has no CI yet. **Heads-up:** later FastAPI / Starlette / python-multipart releases require Python 3.11+, so the next security bump will raise the minimum Python version.

<a id="sec-08"></a>
### SEC-08 — Global login throttle and username timing oracle · Low

- **Where:** `auth/throttle.py`, `transport/api_server.py:253-291`
- **Status:** Fixed · **Known** (README, the global part) · **Verified:** PoC
- **What:** The throttle is one global counter: five wrong attempts from *anyone* lock *everyone* out for 15 minutes; repeating that every 15 minutes keeps the operator out permanently. Separately, the username is compared before the password hash is verified, so an unknown username answers in ~7 ms and the valid one in ~59 ms (measured).
- **Impact:** Cheap denial of service against the UI; username disclosure. Password brute force itself is well contained (≤ 480 guesses per day against argon2).
- **Solution:** Throttle per client IP with exponential backoff and keep only a generous global ceiling; always run a hash verification (against a dummy hash for unknown users); cap the accepted password length (e.g. 1024).
- **Fix (2026-11-16):** The throttle now counts per client address (IPv4 address, or the /64 for IPv6): five failures lock out that client only, so another device can no longer lock the operator out. A global ceiling (20 failures within 15 minutes, all clients together) still bounds guessing from many addresses; the client table is capped at 1024 entries. Every login attempt performs exactly one hash verification — against a dummy hash when the user name is unknown — and the user name is compared in constant time; measured response times are now 73 ms (unknown) vs 65 ms (valid), previously 7 vs 59. Passwords longer than 1024 characters are rejected without hashing on login, setup and password change. Tests: `tests/test_login_throttle.py`. **Deviation:** fixed 15-minute lockout as before, no exponential backoff. **Residual:** an attacker with four or more addresses can still trigger the global lockout, and behind a reverse proxy on another host all visitors share one counter.

<a id="sec-09"></a>
### SEC-09 — Sessions cannot be revoked and live 30 days · Low

- **Where:** `auth/session.py:5-7`, `transport/api_server.py:147-151,293-303`
- **Status:** Fixed · **Verified:** PoC (old cookie still accepted after logout)
- **What:** Tokens are stateless and always verified against `REMEMBER_SECONDS` (30 days); `SESSION_SECONDS` is defined but unused, so a login without "Remember me" yields a token that is equally long-lived. Logout only deletes the cookie in the browser. `/logout` is also reachable via GET, so any page can log the operator out with an `<img>` tag.
- **Impact:** A captured cookie (see SEC-05, SEC-12) stays valid for 30 days or until the password changes.
- **Solution:** Put a `remember` flag in the token and enforce 24 h when it is not set; on logout rotate `session_secret` (single user → "log out everywhere", which is fine) or track a server-side session id; keep only `POST /logout`.
- **Fix (2026-11-16):** Tokens now carry a "remember" flag and decide their own lifetime: 24 hours by default, 30 days only when "Remember me" was ticked (tokens issued before this change count as 24-hour ones). Logout rotates the session-signing secret, so every session ends on the server — a copied cookie stops working — and it only does so for a signed-in request, since `/logout` is a public path. `GET /logout` is removed. Verified in Chromium: a cookie captured before logout gets HTTP 401 afterwards. Tests: `tests/test_sessions.py`. **Behaviour change:** logging out on one device signs out all devices, and after this update sessions older than 24 hours without "Remember me" have to sign in once more.

<a id="sec-10"></a>
### SEC-10 — CSRF defence is `SameSite=Lax` only · Low

- **Where:** `transport/api_server.py:153-197` and all `POST /api/*` handlers
- **Status:** Fixed · **Known** (README) · **Verified:** PoC (`text/plain` body with a foreign `Origin` accepted on `/api/schedule`)
- **What:** There is no CSRF token and no `Origin` check, and the JSON handlers call `request.json()` without looking at the content type, so a plain HTML form can deliver a valid body. `SameSite=Lax` stops other *sites*, but not other *origins on the same site*: a different port on the same host, or sibling host names under a private suffix (`cam.home.lan` → `salt.home.lan`).
- **Impact:** Such an origin can blindly trigger `/api/start`, `/api/reset`, `/api/cancel`, `/api/schedule` and — if it can guess alias and folder names — `/api/execute`.
- **Solution:** In the auth middleware, reject state-changing methods whose `Origin` (or `Sec-Fetch-Site`) is not same-origin; switch the cookie to `SameSite=Strict`; require `Content-Type: application/json` on JSON endpoints.
- **Fix (2026-11-16):** The auth middleware rejects every non-GET request that a browser marks as coming from another origin: `Sec-Fetch-Site` when present (HTTPS, localhost), otherwise `Origin` compared with `Host`. This covers `/login`, `/setup` and `/logout` as well. The three JSON endpoints now require `Content-Type: application/json` and a JSON object. New `[server] allowed_origins` setting as an escape hatch for reverse proxies that rewrite `Host`. Verified in Chromium over plain HTTP on a non-localhost name (Origin sent, no `Sec-Fetch-*`): login, schedule, scan, sync, password change and logout all work. Tests: `tests/test_csrf.py`. **Deviation:** the cookie stays `SameSite=Lax` — `Strict` would make every link from another site (a homelab dashboard, a chat message) land on the login page, and the origin check already closes the same-site gap.

<a id="sec-11"></a>
### SEC-11 — State-machine gaps: `/api/reset`, races, unvalidated actions · Low

- **Where:** `transport/api_server.py:357-363,414-428`, `roles/orchestrator.py:399-412,492-502`
- **Status:** Fixed · **Verified:** Code
- **What:**
  - `/api/reset` has no status check and is not used by the UI. Called during a sync it lazily unmounts both sides under the running rsync and sets the status to `idle` while the job continues.
  - `/api/execute` awaits the request body between checking `READY` and setting `SYNCING`; `/api/start` only changes status once the background task runs. Two quick requests can start two concurrent runs.
  - The allowed-action matrix (`ACTION_OPTS`) exists only in the browser. The server accepts any action for any row, e.g. `pull` on a `local_only` folder.
- **Impact:** Transfers can fail midway or land in the bare mount-point directory instead of on the NAS, runs can interleave, and the server relies on the UI for sanity. Authenticated only, or via SEC-10.
- **Solution:** Remove `/api/reset` or allow it only in `idle/ready/done/error`; set the status before the first `await` or guard transitions with an `asyncio.Lock`; validate each action against the row's `diff_status` on the server.
- **Fix (2026-11-16):** `/api/reset` is refused while a run is in flight and holds the runtime while it unmounts. `/api/start` and `/api/execute` now check the status and claim the runtime with no `await` in between, so two requests can no longer both start a run (`/api/execute` reads its body first). The allowed-action table lives in `sync/planner.py` (`ALLOWED_ACTIONS`) and is enforced twice: `/api/execute` rejects the whole request with a clear message if any action does not fit its row, and `run_sync` skips such an action as a backstop for callers that bypass the API. A test keeps the server table and the UI's `ACTION_OPTS` from drifting apart. Malformed action payloads now return 400 instead of 500. Tests: `tests/test_state_machine.py`. **Deviation:** `/api/reset` was kept (guarded) rather than removed, in case anything outside the UI relies on it.

<a id="sec-12"></a>
### SEC-12 — Secrets on disk are world-readable · Low

- **Where:** `auth/store.py:46-56`; also `state/json_store.py:109`, `schedule/store.py:36`, `config/config.toml` (created by `bootstrap.sh:60-62`)
- **Status:** Fixed · **Verified:** PoC (`data/auth.toml` created with mode `0644`)
- **What:** `auth.toml` holds the argon2 hash *and* the session-signing secret and is written with `Path.write_text()`, i.e. default umask. `config.toml` holds the agent API key and is a plain `cp`. Writes are not atomic, so a crash mid-write can corrupt the file and lock the operator out.
- **Impact:** Any local user on the host can read the signing secret and mint a valid admin cookie. Low on a single-purpose container, real on a shared host.
- **Solution:** Write through a temp file opened with mode `0600` and `os.replace()` it; create `data/` with `0700`; `chmod 600 config/config.toml` in bootstrap; set `UMask=0077` in the systemd unit.
- **Fix (2026-11-16):** New `write_private()` helper writes through a temp file and `os.replace()`, always mode `0600`, independent of umask. `auth.toml`, `state.json`, `schedule.json` and snapshots all use it. An `auth.toml` left at `0644` by an older version is tightened the first time it is read. A data dir the app creates itself gets `0700`; `bootstrap.sh` now runs `chmod 700 data` and `chmod 600 config/config.toml`. At startup the app logs a warning when the config file holds an API key and is readable by other users. Tests: `tests/test_private_files.py`. **Existing installs:** state files become private as they are rewritten; to close the gap at once run `chmod -R go-rwx data && chmod 600 config/config.toml` on both nodes. **Deviation:** an existing data dir is not re-moded automatically (it may be shared on purpose), and `UMask=0077` for the systemd unit is left to SEC-16.

<a id="sec-13"></a>
### SEC-13 — No security headers, inline scripts, third-party fonts · Low

- **Where:** `transport/api_server.py` (no header middleware), `templates/index.html:12,542-1211`, `templates/_auth_base.html:12`
- **Status:** Open · **Verified:** PoC (no `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` or `Cache-Control` on responses)
- **What:** No CSP, no framing protection, no `nosniff`. All JavaScript is inline, which makes a strict CSP impossible today. Both templates load Google Fonts, so every page view of an internal admin tool calls a third party (and the UI degrades offline). The `Host` header is not validated (DNS-rebinding surface for the public `/login` and `/setup` pages).
- **Impact:** No second line of defence for bugs like SEC-02; the UI can be framed by same-site origins.
- **Solution:** Add the headers in the existing ASGI middleware (`frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` on authenticated responses). Move the inline script and handlers to `/static/app.js` so that `script-src 'self'` becomes possible. Self-host the two fonts. Optionally add `TrustedHostMiddleware` with a configured host list.

<a id="sec-14"></a>
### SEC-14 — Orchestrator key is an unrestricted root shell on the agent · Low

- **Where:** `sync/executor.py:176-193`, `config/models.py:69-70`, `README.md:79,308-342`
- **Status:** Open · **Known** (README) · **Verified:** Code
- **What:** rsync and the remote delete run as `root@agent` with a key that carries no restrictions; delete is literally `ssh … rm -rf <path>`. The README has the operator enable `PermitRootLogin yes` and `PasswordAuthentication yes` and makes switching password login off again optional.
- **Impact:** Compromise of the orchestrator is automatically full compromise of the agent host. A forgotten `PasswordAuthentication yes` leaves root password login open on the tailnet / LAN.
- **Solution:**
  1. README: use `PermitRootLogin prohibit-password`, and make disabling password authentication a required last step.
  2. Restrict the key in `authorized_keys`: `restrict,command="rrsync /mnt/salt-and-soil" ssh-ed25519 …`.
  3. To make (2) possible, move delete from SSH to an authenticated agent endpoint (`POST /delete {alias, folder}`), which resolves and validates the path on the agent itself.
  4. Longer term: a dedicated non-root sync user.

<a id="sec-15"></a>
### SEC-15 — rsync and NFS hardening · Low

- **Where:** `sync/executor.py:106-109`, `mounts/nfs.py:46-48`, `config/models.py:23`
- **Status:** Open · **Verified:** Docs (no rsync / NFS available in the audit sandbox)
- **What:**
  - rsync is called without `-s` / `--protect-args`. Since rsync 3.2.4 (Debian 12 ships 3.2.7) shell-active characters in remote paths are escaped by default, so the reference setup is fine. Older rsync versions pass the path through the remote shell unescaped — there a folder named like a shell expression would execute as root on the agent. Folder names are created by ordinary NAS users, so they are not fully trusted input. Not tested here: how folder names containing `*`, `?` or `[` behave on the remote side — worth a test case.
  - NFS shares are mounted without `nosuid,nodev,noexec`, and `rsync -a` as root faithfully recreates device nodes and setuid bits from the other side.
- **Solution:** Add `-s` to the rsync command; add `nosuid,nodev,noexec` to the default `nfs_options`; consider `--no-devices --no-specials`.

<a id="sec-16"></a>
### SEC-16 — Root service without sandboxing, wide bind addresses · Low

- **Where:** `scripts/install-service.sh:18-27`, `config/models.py:15`, `config/loader.py:53`, `scripts/test_scan.py:288-295`
- **Status:** Open · **Known** (README, the root / privileged part) · **Verified:** Code
- **What:** The service runs as root in a privileged container with no systemd hardening. Both roles bind `0.0.0.0` by default. `test-mount` starts a UI with **no authentication** on `0.0.0.0` while printing a `localhost` URL.
- **Solution:**
  1. Bind the agent to its Tailscale address; document `host = "127.0.0.1"` for the orchestrator when it sits behind a TLS proxy.
  2. Bind `test-mount` to `127.0.0.1`.
  3. Add to the unit: `NoNewPrivileges=yes`, `UMask=0077`, `LockPersonality=yes`, `SystemCallArchitectures=native`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK`.
  4. **Caution:** options that give the service its own mount namespace (`ProtectSystem`, `ProtectHome`, `PrivateTmp`, …) can hide the NFS mount from the rsync process that sshd starts on the agent. Test on the agent before enabling any of them.

<a id="sec-17"></a>
### SEC-17 — Scheduled runs sync unattended, contrary to the README · Low

- **Where:** `roles/orchestrator.py:188-212` vs. `README.md:43,378`
- **Status:** Open · **Verified:** Code
- **What:** The README promises that a scheduled run only scans and waits for the operator. `run_scheduled_cycle()` actually pushes every `needs_sync` folder to the remote.
- **Impact:** Integrity rather than access: if the local copy is damaged (ransomware, accidental mass edit), the next scheduled run overwrites the off-site copy with no human in the loop — the scenario an off-site copy is meant to survive. It also means SEC-10's `/api/schedule` is enough to start unattended writes.
- **Solution:** Add `schedule.auto_sync` (default `false`) and only sync when it is enabled, or correct the README. Consider `--backup --backup-dir` for unattended runs so that overwritten files stay recoverable.

<a id="sec-18"></a>
### SEC-18 — Config validation gaps · Info

- **Where:** `config/loader.py:165-177`
- **Status:** Fixed · **Verified:** PoC (`local_path = "/etc"` resolves to `/etc`)
- **What:** The loader rejects `..` in `local_path` / `remote_path`, but an absolute path passes and replaces the mount point entirely in `posixpath.join()`. Aliases accept any characters. The config file is root-owned, so this is a guard-rail issue rather than an attack path.
- **Solution:** Reject absolute paths and normalise; restrict aliases to a conservative character set; validate `host` / `port` values.
- **Fix (2026-11-16):** The loader now rejects absolute `local_path` / `remote_path` (in addition to `..`) and normalises both (`videos/` → `videos`, `.` → share root). Host-like values (`server.host`, agent `host` / `ssh_host` / `ssh_user`, source `local_host`) may not start with `-` or contain spaces, `/` or control characters, since they become arguments of ssh, ping and mount. Ports must be 1–65535. Share paths may not contain control characters. Tests added to `tests/test_config.py`. **Deviation:** aliases are only limited to 64 characters without control characters — a stricter character set would reject legitimate aliases such as `TV Series` or `Foto's & Films`, and aliases are escaped everywhere they are used.

<a id="sec-19"></a>
### SEC-19 — Supply chain of the install path · Info

- **Where:** `README.md:149,172`, `scripts/bootstrap.sh:42-44`, `requirements.txt`
- **Status:** Open · **Verified:** Code
- **What:** Installation is `curl | tar xz` and (for Tailscale) `curl | sh` without checksum verification; `pip install -r requirements.txt` runs as root without hashes; transitive dependencies (e.g. Starlette, see SEC-07) are not pinned.
- **Solution:** Publish a SHA-256 next to each release tarball and verify it in the README snippet; generate a fully pinned, hashed lock file (`pip-compile --generate-hashes`) and install with `--require-hashes`.

---

## Already in good shape

Checked and found sound — worth keeping this way:

- **Password storage:** argon2 via `argon2-cffi` with library defaults; 256-bit random session secret, rotated on every password change; changing the password requires the current one.
- **No shell injection:** every subprocess uses an argument list (`create_subprocess_exec`), never `shell=True`; the one remote shell command quotes its path with `shlex.quote`.
- **Browser cannot inject paths:** `/api/execute` only matches posted actions against server-side diffs.
- **Auth middleware is default-deny:** exact-match allow-list of public paths; the `/static` mount cannot be used to reach API routes.
- **Output encoding:** Jinja2 autoescape is on; folder names and aliases go through `esc()`; log lines use `textContent`. SEC-02 is the single exception.
- **No CORS headers:** other origins cannot read API responses.
- **Symlinks** at the top level are skipped by both scanners.
- **Repo hygiene:** `config.toml`, `data/` and `.env` are git-ignored; no keys, passwords or tokens in any of the 62 commits.

## Suggested order of work

1. **Quick wins, no behaviour change:** SEC-02 (one line), SEC-03 (validation + tests), SEC-12 (file modes), SEC-11 (guard `/api/reset`), SEC-15 (`-s`, mount flags), SEC-07 (version bumps).
2. **Auth hardening, small behaviour changes:** SEC-01 (fail-closed agent key), SEC-04 (host keys), SEC-06 (setup token), SEC-08, SEC-09, SEC-10.
3. **Structural:** SEC-05 (TLS), SEC-13 (CSP needs the JS moved out of the template), SEC-14 (no root shell), SEC-16, SEC-17.

After step 1 the remaining agent-related findings (SEC-02, SEC-03) no longer depend on trusting the agent or the network path.

## Not covered

- The live deployment: NAS export rules, Tailscale ACLs, the actual `sshd_config` and firewall on both hosts.
- Behaviour against real NFS shares and a real rsync/SSH peer (neither is available in the audit sandbox; SEC-15 rests on upstream documentation).
- Host and physical security of the two sites.
