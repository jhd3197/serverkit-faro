#!/usr/bin/env bash
# Builds dist/<name>-<version>.zip from the plugin source.
#
# Usage:
#   ./scripts/build-zip.sh
#
# Reads plugin.json for name/version, stages files in a temp dir (so dev
# junk like .git, node_modules, dist/ doesn't bloat the bundle), and
# writes dist/<name>-<version>.zip ready for the panel's "Upload Zip"
# install path.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f plugin.json ]]; then
    echo "plugin.json not found in $(pwd)" >&2
    exit 1
fi

# Use python (every dev box has it) for robust JSON parsing — avoids the
# usual jq / sed shell-tax.
read -r NAME VERSION < <(python3 - <<'PY'
import json, sys
m = json.load(open('plugin.json'))
if 'name' not in m or 'version' not in m:
    sys.exit("plugin.json must define both 'name' and 'version'")
print(m['name'], m['version'])
PY
)

DIST_DIR="dist"
ZIP_PATH="$DIST_DIR/${NAME}-${VERSION}.zip"

mkdir -p "$DIST_DIR"
rm -f "$ZIP_PATH"

# Build the exclude list for `zip -x`. Mirror what the panel's installer
# also strips, but cleaner bundles are nicer on download.
EXCLUDES=(
    ".git/*" ".github/*" "node_modules/*" "*/node_modules/*" "__pycache__/*"
    ".venv/*" "venv/*" "dist/*" "build/*" ".pytest_cache/*"
    ".idea/*" ".vscode/*" "scripts/*"
    "*.pyc" "*.pyo" "*.log"
)

if ! command -v zip >/dev/null 2>&1; then
    echo "zip not on PATH (apt install zip / brew install zip)" >&2
    exit 1
fi

zip -r "$ZIP_PATH" . -x "${EXCLUDES[@]}" >/dev/null

SIZE_KB=$(du -k "$ZIP_PATH" | awk '{print $1}')
echo "Built $ZIP_PATH (${SIZE_KB} KB)"
echo "Upload via panel → Marketplace → Plugins → Upload Zip"
