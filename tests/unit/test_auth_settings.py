from pathlib import Path

import pytest

from wildcam_bridge.auth import KEY_PREFIX, generate_api_key, hash_api_key, verify_api_key
from wildcam_bridge.settings import Settings


def test_generated_keys_are_unique_and_long() -> None:
    a, b = generate_api_key(), generate_api_key()
    assert a != b
    assert a.startswith(KEY_PREFIX)
    assert len(a) > 40


def test_verify() -> None:
    key = generate_api_key()
    hashes = [hash_api_key(generate_api_key()), hash_api_key(key)]
    assert verify_api_key(key, hashes)
    assert not verify_api_key(key + "x", hashes)
    assert not verify_api_key("", hashes)
    assert not verify_api_key(key, [])
    assert hash_api_key(key).startswith("sha256:")


def test_settings_from_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "ERDA_SHARELINK=abc   # inline comment\n"
        "BRIDGE_API_KEY_HASHES=sha256:aa, sha256:bb ,\n"
        "BRIDGE_ROOT_PATH=bridge/\n"
        "LS_URL=http://127.0.0.1:8081/\n"
        "DISK_RESERVE_GB=1.5\n"
    )
    s = Settings(_env_file=env)  # type: ignore[call-arg]
    assert s.erda_sharelink.get_secret_value() == "abc"
    assert s.api_key_hashes == ["sha256:aa", "sha256:bb"]
    assert s.bridge_root_path == "/bridge"
    assert s.ls_url == "http://127.0.0.1:8081"
    assert s.disk_reserve_bytes == 1_500_000_000
    assert "abc" not in repr(s)  # secrets are masked


def test_root_path_can_be_empty() -> None:
    assert Settings(_env_file=None, bridge_root_path="/").bridge_root_path == ""  # type: ignore[call-arg]


def test_bad_subdir_rejected() -> None:
    with pytest.raises(ValueError, match="ls_data_subdir"):
        Settings(_env_file=None, ls_data_subdir="../etc")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------- gen-api-key .env
H1, H2, NEW = "sha256:" + "1" * 64, "sha256:" + "2" * 64, "sha256:" + "f" * 64


@pytest.mark.parametrize(
    ("line", "expected_line"),
    [
        (  # the production format: one key with a label
            f"BRIDGE_API_KEY_HASHES={H1}   # sql-client (2026-11-12)",
            f"BRIDGE_API_KEY_HASHES={H1},{NEW}   # sql-client (2026-11-12), lab (DATE)",
        ),
        (  # .env.example template: the explanatory comment is replaced
            "BRIDGE_API_KEY_HASHES=                # SECRET-ish: comma-separated sha256:...",
            f"BRIDGE_API_KEY_HASHES={NEW}   # lab (DATE)",
        ),
        (
            f'BRIDGE_API_KEY_HASHES="{H1},{H2}"',
            f'BRIDGE_API_KEY_HASHES="{H1},{H2},{NEW}"   # lab (DATE)',
        ),
        (
            f"export BRIDGE_API_KEY_HASHES = {H1},",
            f"export BRIDGE_API_KEY_HASHES = {H1},{NEW}   # lab (DATE)",
        ),
    ],
)
def test_add_hash_to_env_file(tmp_path: Path, line: str, expected_line: str) -> None:
    from datetime import UTC, datetime

    from wildcam_bridge.auth import add_hash_to_env_file

    env = tmp_path / ".env"
    env.write_text(f"# header\nLS_URL=http://ls\n{line}\nDISK_RESERVE_GB=10\n")
    env.chmod(0o640)
    hashes = add_hash_to_env_file(env, NEW, "lab")
    assert hashes[-1] == NEW
    today = datetime.now(UTC).date().isoformat()
    assert env.read_text().splitlines() == [
        "# header",
        "LS_URL=http://ls",
        expected_line.replace("DATE", today),
        "DISK_RESERVE_GB=10",
    ]
    assert (env.stat().st_mode & 0o777) == 0o640  # mode kept
    assert Settings(_env_file=env).api_key_hashes == hashes  # type: ignore[call-arg]
    assert [p.name for p in tmp_path.iterdir()] == [".env"]  # no temp file left


def test_add_hash_appends_missing_line_and_rejects_bad_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wildcam_bridge import auth

    env = tmp_path / ".env"
    env.write_text("LS_URL=http://ls")  # no trailing newline, no hashes line
    assert auth.add_hash_to_env_file(env, NEW, "db") == [NEW]
    assert env.read_text().startswith(f"LS_URL=http://ls\nBRIDGE_API_KEY_HASHES={NEW}   # db (")
    with pytest.raises(ValueError, match="invalid key name"):
        auth.add_hash_to_env_file(env, H1, "x\nBRIDGE_API_KEY_HASHES=evil")

    # if the re-read does not give old + new exactly, the original file is restored
    before = env.read_text()
    monkeypatch.setattr(auth, "_hashes_in", lambda path: [])
    with pytest.raises(ValueError, match="restored"):
        auth.add_hash_to_env_file(env, H1, "db2")
    assert env.read_text() == before
