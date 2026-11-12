# 03: Repository and tooling

**Date:** 2026-11-12 · **Status:** done

## Goal
Create the git repository with good code practices from the start.

## Done
* Repo `~/wildcam-ls-bridge` (branch `main`, no remote yet). Name: it bridges the *wildcam*
  database and *Label Studio*.
* `uv` project (`pyproject.toml`, `uv.lock`), Python ≥ 3.12, src layout, console script
  `wildcam-bridge`.
* Quality gates: **ruff** (lint incl. bandit security rules, isort, bugbear; formatter),
  **mypy --strict**, **pytest** (+ coverage), **pre-commit** (local hooks: ruff, mypy,
  secret scan, block `.env`, block files > 2 MB), GitHub Actions CI (`.github/workflows/ci.yml`),
  `Makefile` (note: `make` is not installed on the server; the docs list the `uv run` equivalents).
* Secrets: `.env` (chmod 600, git-ignored), template `.env.example`;
  `scripts/check_no_secrets.py` fails if any secret *value* from `.env` appears in a file.
* `.gitignore` also excludes `var/` (runtime state), videos and data folders.
* Git identity for this repo: set locally (`git config user.*`).

## Open
* Add a remote (GitHub/GitLab) later. If it is GitLab, translate `ci.yml` to `.gitlab-ci.yml`.
