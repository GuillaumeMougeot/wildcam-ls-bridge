# 12: GitHub remote

**Date:** 2026-11-13 · **Status:** done ✅

## Request
"I have created a remote on GitHub. Push to it, use main; we are the only devs so far."
Remote: `git@github.com:GuillaumeMougeot/wildcam-ls-bridge.git`.

## Before pushing
* Scanned the whole history for the secret values from `.env` (`ERDA_SHARELINK`,
  `LS_API_TOKEN`) and `secrets/` (client API key), compared without printing them: none
  found. No `.env`, `secrets/`, `var/`, video or database file is tracked.
* GitHub host key added on first contact; its fingerprint matches the one GitHub publishes
  (`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`, ED25519). The server's
  `~/.ssh/id_ed25519` authenticates as `GuillaumeMougeot`.

## Push
`git remote add origin …` then `git push -u origin main`: the remote was empty, so `main` was
created and is tracked. Direct pushes to `main` (no PR flow) while the team is one person.

## CI check
`gh` is not installed on the server, so the GitHub Actions run was not read from here.
The CI job was reproduced on a fresh clone from GitHub (no `.env`, `secrets/` or `var/`):
`uv sync --locked`, ruff, ruff format, mypy `--strict`, pytest → **116 passed**.

`docs/dev-guide.md` now uses the real clone URL.
