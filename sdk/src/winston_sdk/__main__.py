"""`python -m winston_sdk describe TARGET` prints what an extension offers, as JSON, without secrets,
a token or the network. `python -m winston_sdk run TARGET` serves it over MCP on stdio; winstond
starts it that way with WINSTON_API, WINSTON_TOKEN and WINSTON_STATE set. `python -m winston_sdk test
TARGET` runs its tests (`<name>_test.py` beside a file, `test_*.py` and `*_test.py` in a folder, or
the test file given), in whatever environment it is started in: winstond's CLI starts it in the
extension's own when it needs packages Winston does not have."""
from __future__ import annotations

import json
import os
import sys
import tempfile

from winston_sdk.context import stderr_log
from winston_sdk.loader import LoadError, load
from winston_sdk.manifest import API_MAJOR, ManifestError
from winston_sdk.runtime import Runtime
from winston_sdk.services import NoServices, RestServices

USAGE = "usage: python -m winston_sdk describe|run|test TARGET   (a .py file, a folder, or module:attr)"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("describe", "run", "test"):
        print(USAGE, file=sys.stderr)
        return 2
    command, target = argv
    if command == "test":
        from winston_sdk.testing import run_tests, tests_for
        files = tests_for(target)
        if not files:
            print(f"no tests for {target} (a <name>_test.py beside it, or test_*.py in its folder)", file=sys.stderr)
            return 1
        failed = 0
        for f in files:
            print(f"=== {f}", flush=True)
            failed += run_tests(f)
        return 1 if failed else 0
    try:
        ext = load(target)
    except (LoadError, ManifestError, SyntaxError, ImportError, ValueError, TypeError) as e:
        print(f"error {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if ext.manifest.api != API_MAJOR:
        print(f"error written for SDK api {ext.manifest.api}; this is api {API_MAJOR}", file=sys.stderr)
        return 1
    if command == "describe":
        with tempfile.TemporaryDirectory() as state:
            rt = Runtime(ext, services=NoServices(), state_dir=state)
            print(json.dumps(rt.describe(), default=str))
        return 0
    api, token = os.environ.get("WINSTON_API"), os.environ.get("WINSTON_TOKEN")
    if not api or not token:
        print("error WINSTON_API and WINSTON_TOKEN are set by winstond when it starts an extension",
              file=sys.stderr)
        return 1
    from winston_sdk.server import build
    rt = Runtime(ext, services=RestServices(api, token), log_write=stderr_log(ext.manifest.id))
    build(rt).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
