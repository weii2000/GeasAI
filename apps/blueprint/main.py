import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    environment = os.environ | {"GEAS_PYTHON": sys.executable}
    return subprocess.run(
        [
            "npm",
            "--silent",
            "--prefix",
            str(Path(__file__).with_name("tui")),
            "run",
            "start",
        ],
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
