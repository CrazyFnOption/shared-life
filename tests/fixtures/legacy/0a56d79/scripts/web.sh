#!/bin/sh
# Reuse a suitable installed Node. Do not change the user's global runtime.
set -eu
cd "$(dirname "$0")/.."
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' 2>/dev/null; then
  bundled="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"
  if [ -x "$bundled/node" ]; then
    PATH="$bundled:$PATH"
    export PATH
  fi
fi
cd web
exec npm "$@"
