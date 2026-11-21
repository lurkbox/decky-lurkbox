#!/bin/sh
# Builds the installable plugin zip: out/decky-lurkbox-<version>.zip containing Lurkbox/{plugin.json, package.json,
# main.py, py_modules/, dist/index.js, LICENSE, NOTICE-decky-plugin-template, README.md}.
# Usage: sh package.sh            (installs dependencies and builds first)
#        SKIP_BUILD=1 sh package.sh
set -eu
cd "$(dirname "$0")"

VERSION=$(python3 -c 'import json; print(json.load(open("package.json"))["version"])')
PLUGIN_VERSION=$(python3 -c 'import json; print(json.load(open("plugin.json")).get("version", ""))')
if [ "$VERSION" != "$PLUGIN_VERSION" ]; then
  echo "package.json version ($VERSION) and plugin.json version ($PLUGIN_VERSION) differ" >&2
  exit 1
fi

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  pnpm install --frozen-lockfile
  pnpm build
fi
[ -f dist/index.js ] || { echo "dist/index.js is missing; run pnpm build" >&2; exit 1; }

STAGE=out/stage
PKG="$STAGE/Lurkbox"
ZIP="out/decky-lurkbox-$VERSION.zip"
rm -rf "$STAGE" "$ZIP"
mkdir -p "$PKG/dist" "$PKG/py_modules"
command cp plugin.json package.json main.py LICENSE NOTICE-decky-plugin-template README.md "$PKG/"
command cp dist/index.js "$PKG/dist/"
command cp py_modules/*.py "$PKG/py_modules/"

python3 - "$STAGE" "$ZIP" <<'PY'
import os, sys, zipfile
stage, out = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(stage):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(root, f)
            z.write(full, os.path.relpath(full, stage))
PY
rm -rf "$STAGE"
echo "$ZIP"
