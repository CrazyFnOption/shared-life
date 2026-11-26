"""Run browser checks with a fresh fictional ledger and an owned HTTP process.

Default: test the current project after building web/dist. Frozen acceptance:
E2E_CANDIDATE=/absolute/archive NODE_PATH=/path/to/node_modules python tests/e2e/run_browser.py
All following arguments are forwarded to node before the test filename.
"""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/integration"))
from test_http_contract import LiveLedger  # noqa: E402


def main():
    candidate = Path(os.environ.get("E2E_CANDIDATE", ROOT)).resolve()
    with TemporaryDirectory(prefix="shared-expense-browser-") as temporary:
        ledger = LiveLedger(candidate, Path(temporary))
        try:
            subprocess.run([sys.executable, "-m", "scripts.seed_demo", "--database", str(ledger.database)],
                           cwd=candidate, env=ledger.env, check=True)
            ledger.start()
            print(f"Browser candidate: {candidate}; origin: {ledger.origin}", flush=True)
            result = subprocess.run(["node", "--test", *sys.argv[1:], str(Path(os.environ.get("E2E_SPEC", ROOT / "tests/e2e/browser-contract.cjs")).resolve())],
                                    cwd=ROOT, env=dict(os.environ, E2E_ORIGIN=ledger.origin))
            return result.returncode
        finally:
            ledger.stop()
            ledger.log.close()


if __name__ == "__main__":
    sys.exit(main())
