#!/usr/bin/env bash
set -uo pipefail

mc_report() {
  local python
  python=$(command -v python3) || { echo 'memcap report requires Python 3.9+' >&2; return 1; }
  "$python" "$LIB/report.py" --memcap-version "$MEMCAP_VERSION" "$@"
}
