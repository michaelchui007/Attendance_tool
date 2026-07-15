#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi
if [[ ! -x "$PYTHON" ]]; then
  RESOLVED_PYTHON="$(command -v "$PYTHON" || true)"
  if [[ -z "$RESOLVED_PYTHON" ]]; then
    echo "Python interpreter not found: $PYTHON" >&2
    exit 1
  fi
  PYTHON="$RESOLVED_PYTHON"
fi
DIST="$ROOT/build/sidecar-macos"
RESOURCE_DIR="$ROOT/apps/desktop/src-tauri/resources/attendance-engine"

echo "Python: $PYTHON"
"$PYTHON" -m pip install --upgrade pip setuptools wheel
"$PYTHON" -m pip install --no-build-isolation -e "$ROOT[dev]"
rm -rf "$DIST" "$ROOT/build/pyinstaller-macos"
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name attendance-engine \
  --paths "$ROOT" \
  --distpath "$DIST" \
  --workpath "$ROOT/build/pyinstaller-macos" \
  --specpath "$ROOT/build" \
  "$ROOT/scripts/sidecar_entry.py"

rm -rf "$RESOURCE_DIR"
mkdir -p "$(dirname "$RESOURCE_DIR")"
cp -R "$DIST/attendance-engine" "$RESOURCE_DIR"
chmod +x "$RESOURCE_DIR/attendance-engine"
ENGINE_HELP="$("$RESOURCE_DIR/attendance-engine" --help)"
if [[ "$ENGINE_HELP" != *"serve"* ]]; then
  echo "Sidecar smoke test failed: serve command is unavailable." >&2
  exit 1
fi
echo "Sidecar resource: $RESOURCE_DIR"
