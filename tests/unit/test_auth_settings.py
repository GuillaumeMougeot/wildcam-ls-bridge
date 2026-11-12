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
