from pathlib import Path

import pytest

from scripts.check_no_secrets import load_secrets, main


def test_detects_secret_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".env").write_text(
        "ERDA_SHARELINK=AbCdEf1234   # comment\n"
        "LS_API_TOKEN=\n"
        "BRIDGE_PORT=8090\n"
        "# LS_API_TOKEN=commented-out-value\n"
    )
    monkeypatch.chdir(tmp_path)
    assert load_secrets(Path(".env")) == {"ERDA_SHARELINK": "AbCdEf1234"}
    (tmp_path / "ok.py").write_text("SHARE = os.environ['ERDA_SHARELINK']\n")
    (tmp_path / "leak.py").write_text('SFTP_USER = "AbCdEf1234"\n')
    assert main(["ok.py", ".env", "missing.txt"]) == 0
    assert main(["ok.py", "leak.py"]) == 1
    assert "leak.py: contains the value of ERDA_SHARELINK" in capsys.readouterr().err


def test_no_env_file_means_nothing_to_find(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("anything")
    assert main(["a.txt"]) == 0
