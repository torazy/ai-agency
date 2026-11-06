#!/bin/sh
# Run a Python script with whichever interpreter this machine has.
# macOS / Linux usually have only `python3`; Windows (python.org installer) usually has only `python`
# (and a Microsoft Store stub named `python3` that does not work). Hooks and the board call this so the same
# .claude/settings.json works on every OS. stdin is passed through untouched (hooks read their event from it).
#   usage: sh tools/py.sh tools/hooks/guardrail.py [args...]
for exe in python3 python; do
  if command -v "$exe" >/dev/null 2>&1 && "$exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" </dev/null >/dev/null 2>&1; then
    exec "$exe" "$@"
  fi
done
echo "py.sh: Python 3.9+ not found (install Python 3 and make python3 or python available on PATH)" >&2
# Unattended runs (scheduled dispatch, work sessions, morning brief) must not run tools unchecked when the guard cannot start.
if [ -n "$AI_AGENCY_UNATTENDED" ]; then exit 2; fi
exit 0
