"""Copy the selected Railway DB's public URL into private local configuration.

Never dump CLI variable output: it contains raw credentials. This helper captures
it in memory and writes only the selected connection URL to an ignored file.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path

from dotenv import set_key
from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", default="railway")
    parser.add_argument("--service", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--test", action="store_true", help="Write a dedicated test database URL")
    args = parser.parse_args()
    result = subprocess.run(
        [
            args.cli,
            "variable",
            "list",
            "--service",
            args.service,
            "--environment",
            args.environment,
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise SystemExit("Railway variables could not be read; verify CLI login and project link")
    try:
        values = json.loads(result.stdout)
        raw = values["TEST_DATABASE_PUBLIC_URL" if args.test else "BOTFLUENCER_DATABASE_PUBLIC_URL"]
        url = make_url(raw)
        if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
            raise ValueError("Wrong database driver")
        if args.test and not (url.database or "").endswith("_test"):
            raise ValueError("Test database name must end in _test")
        url = url.update_query_dict({"sslmode": "require"})
    except Exception:
        raise SystemExit(
            "Expected a direct public Postgres URL and the correct database name"
        ) from None
    path = Path(".env.test" if args.test else ".env")
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    path.chmod(0o600)
    set_key(
        str(path),
        "TEST_DATABASE_URL" if args.test else "DATABASE_URL",
        url.render_as_string(hide_password=False),
    )
    print(f"Saved private database configuration to {path.name}; credentials were not displayed.")


if __name__ == "__main__":
    main()
