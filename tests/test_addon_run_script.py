import json
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT_DIR / "addon" / "ha-95598" / "run.sh"


def run_addon_script(tmp_path: Path, options: str):
    options_path = tmp_path / "options.json"
    options_path.write_text(options, encoding="utf-8")
    output_path = tmp_path / "environment.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python_shim = bin_dir / "python3"
    python_shim.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "-m" && "$2" == "scripts.main" ]]; then
  "${REAL_PYTHON}" -c 'import json, os; print(json.dumps({key: os.environ.get(key) for key in ("ACCOUNT", "FETCH_ON_STARTUP", "BROWSER_HEADLESS", "DISPLAY", "LOGIN_CREDENTIALS")}))' > "${TEST_OUTPUT_PATH}"
  exit 0
fi
exec "${REAL_PYTHON}" "$@"
""",
        encoding="utf-8",
    )
    python_shim.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "CONFIG_PATH": str(options_path),
            "TEST_OUTPUT_PATH": str(output_path),
            "REAL_PYTHON": sys.executable,
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "DISPLAY": ":99",
        }
    )
    result = subprocess.run(
        ["bash", str(RUN_SCRIPT)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    exported = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else None
    return result, exported


def test_addon_run_script_exports_options_and_headless_mode(tmp_path: Path):
    result, exported = run_addon_script(
        tmp_path,
        json.dumps(
            {
                "account": "account@example.com",
                "fetch_on_startup": True,
                "login_credentials": [{"account": "account@example.com", "password": "password1"}],
            }
        ),
    )

    assert result.returncode == 0, result.stderr
    assert exported == {
        "ACCOUNT": "account@example.com",
        "FETCH_ON_STARTUP": "true",
        "BROWSER_HEADLESS": "true",
        "DISPLAY": None,
        "LOGIN_CREDENTIALS": json.dumps(
            [{"account": "account@example.com", "password": "password1"}], ensure_ascii=False
        ),
    }


def test_addon_run_script_rejects_malformed_options(tmp_path: Path):
    result, exported = run_addon_script(tmp_path, "{not-json")

    assert result.returncode != 0
    assert "Invalid add-on options" in result.stderr
    assert exported is None


def test_addon_run_script_rejects_non_object_options(tmp_path: Path):
    result, exported = run_addon_script(tmp_path, "[]")

    assert result.returncode != 0
    assert "expected a JSON object" in result.stderr
    assert exported is None
