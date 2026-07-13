#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
TARGET_TRIPLE="${TARGET_TRIPLE:-aarch64-apple-darwin}"
DIST="$ROOT/build/sidecar-macos"
BIN_DIR="$ROOT/apps/desktop/src-tauri/binaries"

"$PYTHON" -m pip install --no-build-isolation -e "$ROOT[dev]"
rm -rf "$DIST" "$ROOT/build/pyinstaller-macos"
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name attendance-engine \
  --distpath "$DIST" \
  --workpath "$ROOT/build/pyinstaller-macos" \
  --specpath "$ROOT/build" \
  "$ROOT/scripts/sidecar_entry.py"

mkdir -p "$BIN_DIR"
cp "$DIST/attendance-engine" "$BIN_DIR/attendance-engine-$TARGET_TRIPLE"
chmod +x "$BIN_DIR/attendance-engine-$TARGET_TRIPLE"
echo "Sidecar: $BIN_DIR/attendance-engine-$TARGET_TRIPLE"
