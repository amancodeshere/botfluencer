import os
import subprocess
import sys
from pathlib import Path


def test_invalid_database_configuration_does_not_print_secret() -> None:
    env = os.environ | {
        "DATABASE_URL": "mysql://user:do-not-log-this@localhost/example",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    result = subprocess.run(
        [sys.executable, "-c", "from botfluencer.cli import main; main()", "status"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr
    assert "do-not-log-this" not in result.stderr + result.stdout
