## Summary

<!-- What does this PR change and why? -->

## Security checklist

- [ ] No hardcoded tokens, API keys, or credentials in the diff.
- [ ] New/changed database queries use parameter binding (no f-strings
      or `+` concatenation with user input).
- [ ] New/changed routes that mutate state go through `_mutation_guard`
      (main.py) or an equivalent auth check.
- [ ] New environment variables are documented in `ENV_VARS.md` and
      `.env.geoclaw.example`.

## Review checklist

- [ ] Lint (`ruff check .`) passes locally.
- [ ] Bandit (`bandit -c pyproject.toml -r .`) passes locally.
- [ ] `pip-audit --requirement requirements.txt --strict` passes.
- [ ] I ran the app locally (`uvicorn main:app`) and hit the changed
      endpoints at least once.
