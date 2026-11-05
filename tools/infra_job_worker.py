"""Bootstrap the frozen allocated-job owner without importing from the user's cwd."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from behave.agent.job_worker import main

if __name__ == "__main__":
    raise SystemExit(main())
