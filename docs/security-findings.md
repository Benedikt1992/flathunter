# Security findings

Assessment of the findings reported by the Security workflow (`pip-audit` and `gitleaks`).
Each finding is rated by its actual risk for flathunter, not by the scanner's score:

| Rating | Meaning |
|---|---|
| **high** | Reachable with input an outsider controls, and the damage is serious (code execution, secret leak). |
| **medium** | Reachable, but needs a special position (man in the middle, malicious portal or proxy), or the damage is limited to our own process (DoS). |
| **low** | Only reachable in an unusual setup or with input we control ourselves. |
| **not affected** | flathunter does not use the affected code path. |

Last assessed: 2026-11-21 (#39), against `Pipfile.lock` on `fork-main` at `e4de3dd`.
Baseline: 70 distinct advisories in 22 packages. After the update: 3 (all `soupsieve`, see below).

## Open findings (accepted)

| Package | Advisory | Rating | Why it stays |
|---|---|---|---|
| soupsieve 2.10 | CVE-2026-126190 | low | Matching a selector with a descendant combinator (`a b`) against deeply nested, attacker-controlled HTML takes polynomial time. Portal HTML is untrusted, but flathunter's only CSS selector is `div.in-listingCard` (`crawler/immobiliare.py`), which has no descendant combinator; the other crawlers use `find`/`find_all`, which do not go through soupsieve. The worst case would be a slow crawl, not a leak. The fix (4.0.0) needs Python ≥ 3.11.5, and flathunter still supports 3.10 (CI matrix, `pyproject.toml`). |
| soupsieve 2.10 | CVE-2026-126191, CVE-2026-126193 | not affected | Both need an attacker-controlled **selector** string (regex backtracking, unbounded recursion while parsing). All selectors are constants in our code; neither config nor web UI accept selectors. Fix (3.0.2) also needs Python ≥ 3.11.5. |

Revisit when Python 3.10 support is dropped (then `soupsieve>=4` is possible), or as soon as a
crawler uses descendant-combinator selectors on portal HTML.

## Fixed dependency findings

| Package (old → new) | Advisories | Rating | Reasoning |
|---|---|---|---|
| certifi 2025.10.5 → 2026.11.20 | PYSEC-2026-4320 | medium | The bundle trusted a root whose intermediates have compromised keys. `requests` uses this bundle for every HTTPS call: portals, Telegram, captcha services. A man in the middle with such a certificate could read the Telegram bot token or the captcha API key. |
| urllib3 2.6.2 → 2.8.0 | PYSEC-2026-1996, -142, -4176, -4177 | medium | Decompression bombs and an infinite decode loop when streaming compressed responses. `requests` streams every response body through urllib3 with decoding, and flathunter fetches untrusted portal pages, optionally through free public proxies (`proxies.py`). A malicious server or proxy could hang or exhaust the crawler; read timeouts do not stop the loop. |
| urllib3 | PYSEC-2026-141 | not affected | Header leak on cross-origin redirects in urllib3's own high-level API (`PoolManager.request`). flathunter calls urllib3 only through `requests`, which handles redirects itself. |
| urllib3 | PYSEC-2026-4175 | not affected | Concerns `proxy_ssl_context` / `proxy_assert_*` for HTTPS proxies; flathunter never sets them. |
| requests 2.32.5 → 2.34.2 | PYSEC-2026-2275 | not affected | `extract_zipped_paths()` is only used when `requests` itself is imported from a zip archive; flathunter is installed normally. |
| idna 3.10 → 3.20 | PYSEC-2026-215 | low | CPU exhaustion with crafted Unicode host names. Host names come from our config and from links in portal HTML; a malicious portal could at most slow the crawler. |
| lxml 6.0.2 → 6.1.3 | PYSEC-2026-87 | not affected | Local file disclosure via entity resolution in `iterparse()` and `ETCompatXMLParser`. flathunter only parses HTML (`lxml.html.fromstring`, BeautifulSoup's `lxml` HTML builder), whose default was already safe. |
| soupsieve 2.8 → 2.10 | PYSEC-2026-3071, -3072, -4170, -4171 | not affected | Selector-parser DoS; needs an attacker-controlled selector (see above). |
| soupsieve | CVE-2026-126192 | not affected | Quadratic matching for `:has(a ~ b)`; flathunter uses no `:has()` selector. |
| apprise 1.9.5 → 1.13.1 | PYSEC-2026-4200 | low | HTTP notification plugins re-send credentials when the endpoint redirects. The endpoints are the ones the user configures (`apprise:` URLs); exploiting this needs a malicious or compromised notification endpoint. Stayed on 1.x (2.x is a major release not needed for the fix). |
| flask 3.1.2 → 3.1.3 | PYSEC-2026-2151 | low | Missing `Vary: Cookie` in some session accesses. The web UI uses the session (`web/views.py`), but a leak needs a shared caching proxy in front of it, which no documented deployment has. |
| werkzeug 3.1.4 → 3.1.9 | PYSEC-2026-2044, -2320, -4312 | not affected | `safe_join` and Windows device names; flathunter runs on Linux only (Raspberry Pi, Docker, App Engine). |
| cryptography 46.0.2 → 50.0.2 | PYSEC-2026-35, -36, -2141, -3552, -3553, -3554, GHSA-537c-gmf6-5ccf | low | Only pulled in by `pyjwt`/Google auth for the optional Firestore backend (`googlecloud_idmaintainer.py`). flathunter uses none of the affected APIs (X.509 path validation, PKCS#7 decryption, EC key loading from untrusted input). TLS goes through Python's `ssl`, not through the OpenSSL bundled in cryptography's wheel. The major jump is required by the fixes; wheels exist for `linux/aarch64`. |
| pyjwt 2.10.1 → 2.15.1 | 16 advisories (PYSEC-2026-120, -175 … -179, -4140, -4141, -4144, -4145, -4147 … -4149, -4152, -4183, GHSA-pxh4-856f-4h89), PYSEC-2025-183 | not affected | All concern verifying JWTs or fetching JWKS. `firebase-admin` uses pyjwt for its auth module (ID tokens, session cookies); flathunter only uses Firestore with Application Default Credentials. PYSEC-2025-183 is disputed by the maintainers and no longer reported for 2.15.1. |
| oauthlib 3.3.1 → 4.0.1 | PYSEC-2026-4114, CVE-2026-49272 | not affected | Both are server-side (PKCE check, device-code token endpoint). oauthlib comes in through `requests-oauthlib` (apprise), used as an OAuth client only. |
| anyio 4.11.0 → 4.14.2 | PYSEC-2026-4024, -4025 | not affected | `connect_tcp()` TLS with IDN hosts and process-pool workers. anyio comes in through `httpx` (`firebase-admin`); flathunter uses neither API. |
| h2 4.3.0 → 4.4.1, hpack 4.1.0 → 4.2.0 | PYSEC-2026-3628, PYSEC-2026-4053 | not affected | Duplicate Host headers is a server-side issue; the hpack integer DoS would need a malicious HTTP/2 server, and the only HTTP/2 peers (via `httpx` in `firebase-admin`) are Google endpoints. |
| protobuf 6.32.1 → 6.33.6 | PYSEC-2026-1805 | not affected | Recursion limit bypass in `json_format.ParseDict()`; the Google client libraries parse binary protobuf from Google, not untrusted JSON. Stayed on 6.x. |
| pyasn1 0.6.1 → 0.6.5 | 9 advisories (PYSEC-2026-1810, -2263, -3455 … -3457, CVE-2026-119194 … -119197) | not affected | DoS when decoding attacker-controlled BER/DER. Used by `rsa`/`pyasn1-modules` in Google auth to read our own credentials and Google's certificates. |
| msgpack 1.1.1 → 1.2.4 | PYSEC-2026-3625, CVE-2026-114613 | not affected | Crashes/leaks when unpacking untrusted data. Only used by `CacheControl` (`firebase-admin`) to serialize its own HTTP cache. |
| python-dotenv 1.1.1 → 1.2.4 | PYSEC-2026-2270 | not affected | Symlink following in `set_key()`/`unset_key()`; flathunter only calls `load_dotenv()`. |
| setuptools 80.9.0 → 84.2.1 | PYSEC-2026-3447, GHSA-xmj3-9gf7-h52w | not affected | `MANIFEST.in` handling when building sdists and wheel installation on Windows. At runtime setuptools only provides `distutils` for `undetected-chromedriver`. |
| pytest 8.4.2 → 9.2.0 (dev) | PYSEC-2026-1845 | low | Predictable `/tmp/pytest-of-<user>` directory on multi-user hosts. Development and CI only; CI runners are single-user and ephemeral. |
| pygments 2.19.2 → 2.21.0 (dev) | PYSEC-2026-2987 | not affected | ReDoS in one lexer; pygments is only used by pytest to colour output. |

Most findings are "not affected" and were fixed anyway because the fix versions are
compatible, which keeps the weekly report meaningful. Only the affected packages were
updated; no other package in `Pipfile.lock` changed.

## Secret scan (gitleaks)

All four findings are in old commits only; `gitleaks dir .` on the current tree finds nothing.
History is not rewritten (shared branch, upstream fork), so the findings are listed in
`.gitleaksignore` with the reasoning below.

| Fingerprint | Rating | Reasoning |
|---|---|---|
| `861d245…:flathunter/captcha/twocaptcha_solver.py:generic-api-key:43` and `…:130` | low (for this fork) | A hard-coded `clientKey` for capmonster.cloud in an upstream contribution (2024-08-16). The value has the format of a real key (32 hex characters), so it is treated as a **real secret of a third party**, not as a false positive. It was replaced by `self.api_key` the same day (`c4ac26e`) and has been public in upstream history since 2024. It is not the maintainer's key; flathunter can neither rotate nor revoke it. Rewriting this fork's history would not remove it from upstream. Recommendation: the maintainer decides whether to tell upstream / the contributor so the key gets revoked. |
| `fd8d28e…:test/fixtures/immo-scout-IS24-object.json:gcp-api-key:1` | not affected | `googleMapsApiKey` from a saved ImmoScout24 page (2020). ImmoScout24 embeds this browser key in every public page; such keys are meant to be public and are restricted by the owner (referrer/API restrictions). Not our key. The fixture no longer exists. |
| `fd8d28e…:test/fixtures/immo-scout-IS24-object.json:generic-api-key:1` | not affected | `nonceToken` from the same saved page: a per-page anti-CSRF value of a 2020 anonymous session, not a credential. |
