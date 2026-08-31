"""Start coverage in every interpreter the suite launches as a subprocess.

Python imports `sitecustomize` automatically at startup from anywhere on
`PYTHONPATH`. `conftest.py` puts this directory there and sets
`COVERAGE_PROCESS_START`, so a hook run as `subprocess.run([sys.executable,
HOOK])` — which is how every hook is tested, because "never takes the session
down" cannot be tested in-process — records its lines instead of vanishing.

Without it `hooks/codelayer_gate.py` and `hooks/workflow_post_traverse.py`
reported 0%, and the total read three points below the truth.
"""
from __future__ import annotations

import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except Exception:  # noqa: BLE001 - never break a subprocess under test
        pass
