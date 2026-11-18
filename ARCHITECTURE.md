# Architecture

This document is the source of truth for how flathunter is structured. Humans and
agents both follow it. The dependency rules below are **enforced in CI** by
[import-linter](https://import-linter.readthedocs.io/) (`.importlinter`), so a
violation fails the build instead of relying on review.

Changes to this file, to `.importlinter` or to `docs/adr/` need maintainer
approval (see `.github/CODEOWNERS`). A structural change starts with an ADR.

## What the system does

Flathunter periodically crawls property portals, turns every listing into an
*expose* (a plain `dict`), runs the exposes through a processing pipeline
(dedupe → filter → enrich → notify) and delivers new matches to messengers.

```
 entry points          flathunt.py (CLI loop)   main.py (web app)   cloud_job.py (GCP job)
                              │                        │                    │
 orchestration          Hunter ──────────────── WebHunter (multi-user) ─────┘
                              │
                 ┌────────────┼──────────────────────────────┐
                 ▼            ▼                              ▼
 configuration  Config    ProcessorChain (pipeline)       IdMaintainer / GoogleCloudIdMaintainer
                 │            │                              (persistence: seen ids, exposes, settings)
                 ▼            ├─ SaveAllExposesProcessor
 adapters      Crawlers       ├─ Filter
  (outbound)   (crawler/*)    ├─ AddressResolver / CrawlExposeDetails
                 │            ├─ GMapsDurationProcessor
                 ▼            └─ Notifiers (notifiers/*)
               Captcha solvers (captcha/*)

 shared leaves  logging · exceptions · utils · time_utils
 presentation   web/ (Flask UI, on top of everything)
```

## Components

| Component | Location | Responsibility | Extension point |
|---|---|---|---|
| Entry points | `flathunt.py`, `main.py`, `cloud_job.py` | Wire config, persistence and hunter together; no business logic | – |
| Hunter | `flathunter/hunter.py`, `web_hunter.py` | Orchestrate one crawl → process run | – |
| Config | `flathunter/config.py` | Read YAML + env, decide which crawlers/notifiers/solvers are active | New config keys go here, with defaults, plus `config.yaml.dist` |
| Crawlers | `flathunter/crawler/*` | Fetch and parse **one** portal into exposes | Subclass `abstract_crawler.Crawler` (or `WebdriverCrawler`) and register in `Config` |
| Captcha | `flathunter/captcha/*` | Solve captchas via external services | Subclass `captcha_solver.CaptchaSolver` |
| Processors | `processor.py`, `default_processors.py`, `gmaps_duration_processor.py`, `idmaintainer.py` | Transform/filter a stream of exposes | Subclass `abstract_processor.Processor`, add a builder method to `ProcessorChainBuilder` |
| Filter | `flathunter/filter.py` | Declarative expose filters | Add a filter class and builder method |
| Notifiers | `flathunter/notifiers/*` | Deliver a message to **one** messenger | Subclass `abstract_notifier.Notifier` (+ `Processor`), register in `ProcessorChainBuilder.send_messages` |
| Persistence | `idmaintainer.py` (SQLite), `googlecloud_idmaintainer.py` (Firestore) | Remember seen exposes, user settings, run times | Keep both implementations' interfaces identical |
| Web UI | `flathunter/web/*` | Flask views/stats for the hosted multi-user setup | – |
| Shared leaves | `logging.py`, `exceptions.py`, `utils/`, `time_utils.py` | Cross-cutting helpers | Must stay dependency-free internally |

## Dependency rules (enforced)

1. **Shared leaves depend on nothing internal.** `logging`, `exceptions`, `utils`,
   `time_utils` must not import other flathunter modules.
2. **Crawlers are isolated.** Crawlers (and their base classes) must not import
   notifiers, processors, the hunter, config or the web UI. They receive config
   through their constructor.
3. **Crawlers are independent of each other.** Shared crawling code goes into
   `abstract_crawler.py` / `webdriver_crawler.py`, never crawler → crawler.
4. **Notifiers are isolated and independent.** They must not import crawlers,
   captcha, the hunter, the processor chain or the web UI, nor each other.
5. **Captcha solvers are standalone adapters.** No imports of config, crawlers,
   notifiers, processors, hunter or web.
6. **The web UI is the outermost layer.** Nothing in the core imports `flathunter.web`.

Rules 2, 4 allow *indirect* imports (e.g. a notifier imports `config`, which
imports crawlers) because `config` is the composition root. Direct imports are
what is forbidden.

## Conventions

- **Data model:** an expose is a `dict` with at least `id`, `url`, `title`,
  `price`, `size`, `rooms`, `address`, `crawler`. Keep keys stable; notifiers and
  the web UI depend on them.
- **Errors:** raise exceptions from `flathunter/exceptions.py` (add new ones there);
  crawlers must not crash the hunt loop — catch network errors and return `[]`.
- **Logging:** use `from flathunter.logging import logger`. Never log secrets
  (tokens, API keys, chat ids are fine only at debug level).
- **Config & secrets:** every secret is configurable via env var
  (`FLATHUNTER_*`) as well as YAML. Never hard-code credentials or URLs with tokens.
- **Network in tests:** tests never hit real portals or APIs. Use
  `requests-mock`, fixtures in `test/crawler/fixtures/` and `test/utils/`.
- **Typing:** new and changed code is type-annotated and must pass `pyright`.

## Quality gates

Every pull request must pass, independent of who or what wrote it:

| Gate | Tool | Workflow |
|---|---|---|
| Tests | `pytest` (via `coverage run`) | `tests.yml` |
| Coverage of changed lines ≥ 80 % | `diff-cover` | `tests.yml` |
| Lint | `pylint flathunter` | `linter.yml` |
| Types | `pyright` | `types.yml` |
| Architecture | `lint-imports` | `quality.yml` |
| No weakened gates / suppressions / deleted tests | `.github/scripts/agent_guard.py` | `guard.yml` |
| SAST | `bandit` (medium+) | `security.yml` |
| No new vulnerable dependencies | `pip-audit` diffed against base | `security.yml` |
| No secrets | `gitleaks` | `security.yml` |
| Code scanning | CodeQL | `codeql.yml` |

## Decisions

Architecture decisions are recorded in [`docs/adr/`](docs/adr/).
