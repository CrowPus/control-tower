## What and why

<!-- What this changes and why. Link the issue: Closes #123 -->

## How I checked it

<!-- Tests added or changed; for anything visible, a screenshot. -->

## Checklist

- [ ] `uv run ruff check …`, `uv run mypy app` and `uv run pytest` pass (see CONTRIBUTING.md → Checks)
- [ ] After changing a route: `npm run api` was run and the generated types are committed
- [ ] A new or changed admin page or route has its page in `backend/app/admin_docs/`
- [ ] Docs changed with the behaviour (README, docs/, CHANGELOG.md under *Unreleased*)
- [ ] The rules still hold: agents only propose, the owner decides, budgets apply, personal fields stay hidden
- [ ] No keys, passwords or personal data in code, tests, logs or screenshots
- [ ] Every commit is signed off (`git commit -s`)
- [ ] I have read and accept the [Contributor License Agreement](https://github.com/CrowPus/control-tower/blob/main/CLA.md) <!-- first pull request only -->
