# Working on flathunter

Read `ARCHITECTURE.md` before changing code. It defines the components, the
dependency rules (enforced by `lint-imports`) and the conventions.

## Setup and commands

```bash
pipenv requirements > requirements.txt && pip install -r requirements.txt -r requirements-dev.txt
prek install                              # optional: run the gates as git pre-commit hook

python -m pytest -q                       # tests (no network; use requests-mock + fixtures)
python -m pytest test/test_hunter.py -q   # a single test file
prek run --all-files                      # ALL static gates (~1 s): what CI runs
ruff check <file> / ruff format <file>    # lint / format a single file
pyrefly check                             # strict type check
python .github/scripts/mutation_report.py --base origin/fork-main   # do my tests catch bugs?
```

Run the tests and `prek run --all-files` before every commit. In Claude Code the
project hooks (`.claude/settings.json`) do this automatically: every edited Python
file is formatted and linted, and before you finish all gates run once.

## Quality gates and ratchets

The tooling is strict (Ruff with all rules, Pyrefly strict, cognitive complexity ≤ 15),
but legacy code is grandfathered: existing violations are frozen in baselines
(`quality/*.json`, `complexipy-snapshot.json`). The rule is simple:

- **New or changed code must be clean.** A new violation anywhere fails, even in a
  legacy file. Fix it; never add it to a baseline.
- **Debt may only go down.** If you remove legacy violations, the gates ask you to run
  `python .github/scripts/ratchet.py prune`, which shrinks the baselines. Commit that.
  Baselines can never grow without the maintainer (the guard enforces this).
- Formatting: new files and already-formatted files must be `ruff format`-clean. Do not
  reformat untouched legacy files (that creates noise and upstream merge conflicts).

## Rules for every change

1. **Scope:** implement exactly what the issue's acceptance criteria ask for.
   No drive-by refactors, renames or formatting changes outside the touched code.
   Record anything else you notice as a follow-up in the PR description.
2. **Tests first:** start with a test that fails because the behaviour is missing,
   then implement. Every acceptance criterion maps to at least one test. Tests must
   assert behaviour, not just execute code: mutation testing (`mutation_report.py`)
   shows code your tests don't really check, and the reviewer uses it. For pure
   functions (parsers, filters, formatting) add Hypothesis property tests
   (see `test/test_list_properties.py`). No network access in tests.
3. **Architecture:** respect the dependency rules in `ARCHITECTURE.md`. If the
   issue cannot be solved within them, stop and say so instead of working around them.
4. **Never weaken a gate.** Do not edit `.github/`, `.claude/`, `pyproject.toml`,
   `.pre-commit-config.yaml`, `requirements-dev.txt`, `pytest.ini`, `.importlinter`, `.coveragerc`,
   `quality/` (except `prune`), `CLAUDE.md`, `ARCHITECTURE.md`, `docs/adr/`.
   Do not add suppressions: `# noqa`, `# ruff: noqa`, `# type: ignore`,
   `# pyrefly: ignore`, `# pylint: disable`, `# nosec`, `# fmt: off/skip`,
   `pragma: no cover`, `pragma: no mutate`, `pytest.mark.skip`/`xfail`; and do not
   delete or loosen existing tests. CI (`agent_guard.py`) rejects these unless the
   maintainer explicitly allows them.
5. **Dependencies:** do not add or upgrade packages (`Pipfile`, `Pipfile.lock`,
   `setup.py`, `requirements-dev.txt`) unless the issue explicitly asks for it.
6. **Security:** no secrets in code, tests or logs; validate external input
   (HTML from portals, user settings from the web UI); use timeouts on network calls;
   never use `shell=True`, `eval`, `pickle` on untrusted data, or disable TLS verification.
   Ruff's security rules (S) enforce much of this.
7. **Style:** fully type-annotated, Google-style docstrings on public classes and
   functions, small functions (Ruff + complexipy enforce this for new code).
8. **Commits:** small, focused commits with imperative messages that reference the
   issue (`Add X to Y (#123)`). Never rewrite history on a shared branch.
9. **When unsure:** do not guess requirements. Report open questions; the maintainer
   answers them.

## Roadmap-Regeln für Implementierungs-Agents (aus Epic #1)

Diese Regeln stammen aus dem Roadmap-Epic #1 und gelten zusätzlich zu den obigen.
Bei Widerspruch gilt die strengere Regel.

1. **Ein Issue = ein Branch = ein PR gegen `fork-main`.** Der Agent-Workflow arbeitet
   auf `agent/issue-<nr>` und verknüpft den PR mit `Closes #<nr>`. Ein Issue wird erst
   begonnen, wenn alle »Blockiert durch«-Issues geschlossen und deren PRs gemergt sind
   (der Maintainer setzt `agent:ready` erst dann).
2. **Zuerst lesen:** das Issue vollständig, `CLAUDE.md`, `ARCHITECTURE.md`, die im Issue
   genannten Dateien. Die Kontext-Abschnitte beschreiben den Stand bei Erstellung der
   Issues; bei Abweichungen gilt der aktuelle Code.
3. **Umfang einhalten.** Nur was unter »Aufgaben« steht; »Nicht-Ziele« bleiben unberührt.
   Fällt Zusatzarbeit auf, wird sie im PR benannt, nicht mit erledigt.
4. **Nichts erfinden.** Selektoren, Endpunkte, Feldnamen und Fixtures stammen aus echten
   Antworten des Portals. Ist ein Portal nicht erreichbar oder verlangt eine Anmeldung:
   Draft-PR mit genauer Liste der fehlenden Fixtures samt `scripts/capture_fixture.py`-Aufruf,
   dann stoppen (bzw. `status: blocked` mit dieser Liste melden).
5. **Keine Außenwirkung.** Agents legen keine Konten an, inserieren nichts, senden keine
   echten Bewerbungen, Nachrichten oder E-Mails und rufen in Tests keine kostenpflichtigen
   APIs auf.
6. **Tests offline.** Jede Änderung bringt Tests mit, die ohne Internet laufen. Tests mit
   echtem Netzwerk tragen den Marker `live` (registriert in `pytest.ini`) und laufen nie
   in der Pflicht-CI.
7. **Qualität:** `prek run --all-files` (Ruff, Pyrefly strict, Komplexität,
   Architekturverträge, deptry) und alle Offline-Tests grün; neue Verstöße werden behoben,
   nie in eine Baseline aufgenommen. Überlebende Mutanten in geänderten Funktionen
   (`mutation_report.py`) werden durch Tests getötet oder als äquivalent begründet.
   Ergebnis im PR nennen.
8. **Geheimnisse und personenbezogene Daten** stehen nie im Code, in Fixtures, im Log oder
   im PR. Fixtures werden vor dem Commit von Namen, Telefonnummern und E-Mail-Adressen
   echter Personen bereinigt.
9. **Datenbankänderungen** nur über neue, nummerierte Migrationen; bestehende Migrationen
   werden nie verändert.
10. **Rücksicht gegenüber Portalen:** Alle Abrufe laufen über die Abruf-Schicht (#7) mit
    ihren Limits. Bei einer Sperre wird nicht eskaliert, sondern pausiert und gemeldet.
11. **Abhängigkeiten** nur, wenn das Issue sie vorsieht und sie auf `linux/arm64` ohne
    Kompilieren installierbar sind; jede neue Abhängigkeit im PR begründen. `Pipfile`,
    `Pipfile.lock` und `requirements-dev.txt` sind geschützt: Der Maintainer gibt die
    Änderung mit dem Label `allow-protected-change` frei.
12. **Dokumentation** im selben PR nachziehen (`docs/`, `config.yaml.dist`). Neue
    Komponenten oder Abhängigkeitsregeln gehören nach `ARCHITECTURE.md` und `.importlinter`;
    diese Dateien sind wie `CLAUDE.md` geschützt. Die Änderung wird im PR vorgenommen und
    begründet, der Maintainer gibt sie mit `allow-protected-change` frei.
13. **Bei echten Unklarheiten** oder einer Entscheidung, die über das Issue hinausgeht:
    Frage stellen (`open_questions`) und stoppen, statt zu raten.

## Agent workflow

The automated issue → PR process (labels, review loop, limits) is described in
`docs/agent-workflow.md`. Agent role prompts live in `.github/agents/`. Tooling
decisions: `docs/adr/0002-python-quality-toolchain.md`.
