#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
DIST="$ROOT/build/sidecar-macos"
RESOURCE_DIR="$ROOT/apps/desktop/src-tauri/resources/attendance-engine"

"$PYTHON" -m pip install --no-build-isolation -e "$ROOT[dev]"
rm -rf "$DIST" "$ROOT/build/pyinstaller-macos"
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name attendance-engine \
  --distpath "$DIST" \
  --workpath "$ROOT/build/pyinstaller-macos" \
  --specpath "$ROOT/build" \
  "$ROOT/scripts/sidecar_entry.py"

rm -rf "$RESOURCE_DIR"
mkdir -p "$(dirname "$RESOURCE_DIR")"
cp -R "$DIST/attendance-engine" "$RESOURCE_DIR"
chmod +x "$RESOURCE_DIR/attendance-engine"
echo "Sidecar resource: $RESOURCE_DIR"
