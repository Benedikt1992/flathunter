Closes #

## Summary
<!-- What changed and why -->

## Test plan
<!-- Which tests prove which acceptance criterion -->

## Checklist
- [ ] Every acceptance criterion is covered by a test that fails without this change
- [ ] No surviving mutants in changed functions (`mutation_report.py`), or explained as equivalent
- [ ] `prek run --all-files` passes; baselines only shrank
- [ ] Dependency rules in `ARCHITECTURE.md` respected (`lint-imports`)
- [ ] No suppressions, grown baselines or removed tests (or the matching `allow-*` label is set)
- [ ] Every changed protected file is required by the issue; the reason is given above
- [ ] No secrets, external input validated, timeouts on network calls
- [ ] Out-of-scope ideas listed as follow-ups instead of included

## Follow-ups
