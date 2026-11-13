"""Command line interface: ``wildcam-bridge <command>`` (run ``--help`` for details).

Server-side commands read ``.env`` from the current directory (run them from the repo root).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from wildcam_bridge.auth import generate_api_key, hash_api_key


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1000 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"  # pragma: no cover


# ---------------------------------------------------------------------- commands
def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from wildcam_bridge.api import create_app
    from wildcam_bridge.settings import get_settings

    s = get_settings()
    if not s.api_key_hashes:
        print("BRIDGE_API_KEY_HASHES is empty: every request would be rejected.", file=sys.stderr)
        return 2
    uvicorn.run(
        create_app(s),
        host=s.bridge_host,
        port=s.bridge_port,
        root_path=s.bridge_root_path,  # public prefix (nginx strips it before proxying)
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level=args.log_level,
    )
    return 0


def cmd_gen_api_key(_: argparse.Namespace) -> int:
    key = generate_api_key()
    print("API key (give to the client, shown only once):")
    print(f"  {key}")
    print("Add this hash to BRIDGE_API_KEY_HASHES in .env (comma-separated):")
    print(f"  {hash_api_key(key)}")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Check ERDA connection, file presence and disk space for a CSV, without downloading."""
    from wildcam_bridge import erda
    from wildcam_bridge.csv_ingest import parse_csv
    from wildcam_bridge.jobs import new_job_id
    from wildcam_bridge.settings import get_settings

    s = get_settings()
    parsed = parse_csv(
        Path(args.csv).read_bytes(), strip=s.erda_strip_prefix, max_rows=s.bridge_max_rows
    )
    files = erda.plan_files(s, new_job_id(), parsed.items)
    report = erda.dry_run(s, files)
    result = asyncio.run(report)
    out = result.to_dict()
    out["csv_rows"] = parsed.n_rows
    out["csv_warnings"] = parsed.warnings
    _print(out)
    print(
        f"\n{'OK' if result.ok else 'NOT OK'}: {result.n_found}/{result.n_files} files on ERDA, "
        f"{_human(result.bytes_to_download)} to download, {_human(result.free_bytes)} free "
        f"(reserve {_human(result.reserve_bytes)}).",
        file=sys.stderr,
    )
    return 0 if result.ok and not result.missing else 1


def cmd_run(args: argparse.Namespace) -> int:
    """Run the whole pipeline synchronously for a CSV (no HTTP server needed)."""
    from wildcam_bridge.jobs import JobStore, new_job_id
    from wildcam_bridge.pipeline import Pipeline
    from wildcam_bridge.settings import get_settings

    s = get_settings()
    from wildcam_bridge.csv_ingest import parse_csv

    data = Path(args.csv).read_bytes()
    parsed = parse_csv(data, strip=s.erda_strip_prefix, max_rows=s.bridge_max_rows)
    store = JobStore(s.db_path)
    pipeline = Pipeline(s, store)
    job_id = new_job_id()
    pipeline.save_input(job_id, data)
    store.create(
        job_id=job_id,
        title=args.title or f"wildcam {job_id}",
        description=args.description,
        dry_run=args.dry_run,
        allow_missing=args.allow_missing,
        n_items=len(parsed.items),
        warnings=parsed.warnings,
    )
    pipeline.process(job_id)
    job = store.get(job_id)
    if job is None:  # pragma: no cover - cannot happen, job created above
        return 1
    _print(job.to_public(s.ls_public_url))
    return 0 if job.status in ("ready", "dry_run_ok") else 1


def cmd_check(_: argparse.Namespace) -> int:
    """Verify configuration: Label Studio token, ERDA login, disk space, label config."""
    from wildcam_bridge import erda
    from wildcam_bridge.label_config import load_label_config
    from wildcam_bridge.pipeline import default_ls_factory
    from wildcam_bridge.settings import get_settings

    s = get_settings()
    ok = True

    def report(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        ok &= passed
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    try:
        cfg = load_label_config(s.ls_label_config)
        report(
            "label config", True, f"{len(cfg.controls)} controls ({', '.join(cfg.control_names)})"
        )
    except Exception as exc:
        report("label config", False, str(exc))
    report("api keys", bool(s.api_key_hashes), f"{len(s.api_key_hashes)} key hash(es) configured")
    try:
        with default_ls_factory(s) as ls:
            me = ls.whoami()
        report("label studio", True, f"{s.ls_url} as user id {me.get('id')}")
    except Exception as exc:
        report("label studio", False, str(exc))
    try:
        asyncio.run(erda.check_connection(s))
        report("erda", True, f"logged in to {s.erda_host}:{s.erda_port} (host key pinned)")
    except Exception as exc:
        report("erda", False, str(exc))
    root = s.ls_data_root
    if root.is_dir() and os.access(root, os.W_OK):
        free = erda.disk_free_bytes(root)
        report("disk", free > s.disk_reserve_bytes, f"{_human(free)} free at {root}")
    else:
        report("disk", False, f"{root} missing or not writable")
    return 0 if ok else 1


def cmd_delete(args: argparse.Namespace) -> int:
    """Delete a job's Label Studio project + videos on this server (no API key needed)."""
    from wildcam_bridge.jobs import JobStore
    from wildcam_bridge.pipeline import DeleteRefused, Pipeline
    from wildcam_bridge.settings import get_settings

    s = get_settings()
    store = JobStore(s.db_path)
    job_id = _delete_target(args, store.find_by_project)
    if job_id is None:
        print(f"no bridge job created project {args.project}", file=sys.stderr)
        return 1
    try:
        summary = Pipeline(s, store).delete(job_id, force=args.force)
    except KeyError:
        print(f"unknown job {job_id}", file=sys.stderr)
        return 1
    except DeleteRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    _print({"job_id": job_id, **summary})
    return 0


def _delete_target(args: argparse.Namespace, find_by_project: Callable[[int], Any]) -> str | None:
    if (args.job_id is None) == (args.project is None):
        raise SystemExit("give either a JOB_ID or --project PROJECT_ID")
    if args.job_id is not None:
        return str(args.job_id)
    job = find_by_project(args.project)
    return None if job is None else str(job.id)


# ---------------------------------------------------------------------- client commands
def _client() -> Any:
    from wildcam_bridge.client import BridgeClient

    url, key = os.environ.get("BRIDGE_URL"), os.environ.get("BRIDGE_API_KEY")
    if not url or not key:
        raise SystemExit("set BRIDGE_URL and BRIDGE_API_KEY environment variables")
    return BridgeClient(url, key)


def cmd_client_submit(args: argparse.Namespace) -> int:
    with _client() as c:
        job = c.submit(
            args.csv,
            title=args.title,
            description=args.description,
            dry_run=args.dry_run,
            allow_missing=args.allow_missing,
        )
        if args.wait:
            job = c.wait(job["id"])
    _print(job)
    return 1 if job["status"] == "failed" else 0


def cmd_client_status(args: argparse.Namespace) -> int:
    with _client() as c:
        job = c.job(args.job_id)
        if job["status"] == "ready":
            job["annotation_progress"] = c.progress(args.job_id)
    _print(job)
    return 0


def cmd_client_results(args: argparse.Namespace) -> int:
    with _client() as c:
        if args.format == "csv":
            text = c.results_csv(args.job_id, include_unannotated=args.include_unannotated)
        else:
            text = json.dumps(
                c.results(args.job_id, include_unannotated=args.include_unannotated),
                indent=2,
                ensure_ascii=False,
            )
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def cmd_client_delete(args: argparse.Namespace) -> int:
    import httpx

    if (args.job_id is None) == (args.project is None):
        raise SystemExit("give either a JOB_ID or --project PROJECT_ID")
    with _client() as c:
        try:
            if args.project is not None:
                job = c.delete_project(args.project, force=args.force)
            else:
                job = c.delete(args.job_id, force=args.force)
        except httpx.HTTPStatusError as exc:  # 404 unknown, 409 refused: show the reason
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except ValueError:
                detail = exc.response.text
            print(f"error {exc.response.status_code}: {detail}", file=sys.stderr)
            return 1
    _print(job)
    return 0


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wildcam-bridge", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run the HTTP API (behind nginx)")
    s.add_argument("--log-level", default="info")
    s.set_defaults(func=cmd_serve)

    sub.add_parser("gen-api-key", help="generate a new client API key").set_defaults(
        func=cmd_gen_api_key
    )
    sub.add_parser("check", help="verify configuration and connectivity").set_defaults(
        func=cmd_check
    )

    s = sub.add_parser("dry-run", help="ERDA + disk-space dry run for a CSV (no download)")
    s.add_argument("csv")
    s.set_defaults(func=cmd_dry_run)

    def job_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("csv")
        sp.add_argument("--title", default="")
        sp.add_argument("--description", default="")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--allow-missing", action="store_true")

    s = sub.add_parser("run", help="run the full pipeline locally for a CSV (no API)")
    job_opts(s)
    s.set_defaults(func=cmd_run)

    def delete_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("job_id", nargs="?", help="job id (or use --project)")
        sp.add_argument("--project", type=int, help="Label Studio project id")
        sp.add_argument("--force", action="store_true", help="also if the project has annotations")

    s = sub.add_parser(
        "delete", help="delete a job's Label Studio project and videos on this server"
    )
    delete_opts(s)
    s.set_defaults(func=cmd_delete)

    client = sub.add_parser(
        "client", help="call a remote bridge (needs BRIDGE_URL, BRIDGE_API_KEY)"
    )
    csub = client.add_subparsers(dest="client_command", required=True)
    s = csub.add_parser("submit", help="upload a CSV and create a job")
    job_opts(s)
    s.add_argument("--wait", action="store_true", help="poll until the job is ready/failed")
    s.set_defaults(func=cmd_client_submit)
    s = csub.add_parser("status", help="job status (+ annotation progress when ready)")
    s.add_argument("job_id")
    s.set_defaults(func=cmd_client_status)
    s = csub.add_parser("results", help="download annotation results")
    s.add_argument("job_id")
    s.add_argument("--format", choices=["json", "csv"], default="csv")
    s.add_argument("--include-unannotated", action="store_true")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_client_results)
    s = csub.add_parser("delete", help="delete a job's Label Studio project and videos")
    delete_opts(s)
    s.set_defaults(func=cmd_client_delete)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("asyncssh").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    rc: int = args.func(args)
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
