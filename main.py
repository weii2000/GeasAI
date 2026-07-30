import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    tui_directory = Path(__file__).parent / "tui"
    environment = os.environ | {"GEAS_PYTHON": sys.executable}
    return subprocess.run(
        [
            "npm",
            "--silent",
            "--prefix",
            str(tui_directory),
            "run",
            "start",
        ],
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
