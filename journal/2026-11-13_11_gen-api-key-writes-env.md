# 11: `gen-api-key` registers the key in `.env` itself

**Date:** 2026-11-13 · **Status:** done ✅

## Request
"Can't gen-api-key add the hash itself (if it finds the file)? It could also print the
generated key and the command to restart the service."

## Change
`uv run wildcam-bridge gen-api-key [--name "<who>"] [--env-file .env] [--no-write]`
1. generates the key (`wcb_…`) and prints it (shown once);
2. if `.env` exists (current folder by default), appends the `sha256:…` hash to
   `BRIDGE_API_KEY_HASHES` and adds `<who> (date)` to the line's comment, e.g.
   `BRIDGE_API_KEY_HASHES=sha256:…,sha256:…   # sql-client (2026-11-12), lab laptop (2026-11-13)`.
   Without `.env` (or with `--no-write`), it prints the hash to add by hand;
3. prints `sudo systemctl restart wildcam-bridge`. It does not restart the service itself,
   because that needs sudo.

Safeguards (`auth.add_hash_to_env_file`): the name is restricted to a safe character set
(no newline, no `#`). The write is atomic (temp file + rename), keeps the file mode and leaves
no temp file. Afterwards `.env` is re-read with python-dotenv (the parser pydantic-settings
uses), and the hashes must be exactly the old ones plus the new one; otherwise the original is
restored and the command fails. Only the last `BRIDGE_API_KEY_HASHES` line is edited
(python-dotenv: the last one wins); `export` and quoted values are handled.

## Found on the way
That check caught a bug in the first version of the line parser: in the `.env.example`
template line `BRIDGE_API_KEY_HASHES=      # comment`, it took the comment for the value.
python-dotenv itself reads that line as empty, so nothing was ever misconfigured. The parser
now follows python-dotenv's rule (`#` starts a comment after whitespace or right after `=`).
The first key replaces the template's comment.

## Tests
* Unit: 4 line formats (production format with a label, template, quoted, `export` with a
  trailing comma), missing line, rejected name, restore on a failed check, mode kept, no temp
  file left; CLI with and without `.env` and with `--no-write`.
* The old CLI test ran in the repo folder: with the new behaviour it would have written to
  the real `.env`. All `gen-api-key` tests now run in a temp folder; the real `.env` was not
  touched (mtime unchanged).
* Trial on a throwaway copy of the production `.env`: only the key line changed, mode stayed
  600. **116 passed**; ruff and mypy `--strict` clean.
