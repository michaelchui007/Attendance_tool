#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP="$ROOT/apps/desktop"
APP="$DESKTOP/src-tauri/target/release/bundle/macos/考勤统计.app"
RELEASE_DIR="$ROOT/release"
DMG_STAGE="$ROOT/build/dmg-stage"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org}"
PYTHON="${PYTHON:-python3}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此脚本必须在 macOS 上运行。" >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "当前脚本目标为 Apple Silicon arm64，当前架构：$(uname -m)" >&2
  exit 1
fi
if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.cargo/env"
fi
for command in "$PYTHON" npm cargo codesign hdiutil; do
  command -v "$command" >/dev/null || { echo "缺少命令：$command" >&2; exit 1; }
done
APP_VERSION="$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' "$DESKTOP/src-tauri/tauri.conf.json")"
DMG="$RELEASE_DIR/考勤统计_${APP_VERSION}_aarch64_unsigned.dmg"

"$ROOT/scripts/build_sidecar_macos.sh"
cd "$DESKTOP"
if [[ -f package-lock.json ]]; then
  npm ci --registry="$NPM_REGISTRY" --replace-registry-host=always
else
  npm install --registry="$NPM_REGISTRY" --replace-registry-host=always
fi
npm run tauri build -- --bundles app

# Ad-hoc signing does not create a distributable Developer ID signature, but it
# seals the local bundle and embedded Sidecar so bundle integrity can be checked.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

ENGINE="$APP/Contents/Resources/resources/attendance-engine/attendance-engine"
if [[ ! -x "$ENGINE" ]]; then
  echo "考勤计算引擎不存在或不可执行：$ENGINE" >&2
  exit 1
fi
ENGINE_HELP="$("$ENGINE" --help)"
if [[ "$ENGINE_HELP" != *"serve"* ]]; then
  echo "考勤计算引擎启动检查失败：serve 命令不可用。" >&2
  exit 1
fi

rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE" "$RELEASE_DIR"
cp -R "$APP" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
rm -f "$DMG" "$DMG.sha256"
hdiutil create -volname "考勤统计" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG"
hdiutil verify "$DMG"
shasum -a 256 "$DMG" > "$DMG.sha256"

echo
echo "unsigned DMG 已生成：$DMG"
echo "SHA-256：$(cat "$DMG.sha256")"
cat <<'NOTICE'
注意：该应用只有 ad-hoc 签名，没有 Apple Developer ID 签名和公证。
脚本用于内部测试或让同事在自己的 Mac 上本地构建，不能绕过 Gatekeeper，
也不能替代正式公开发布所需的 Developer ID signing 与 notarization。
NOTICE
