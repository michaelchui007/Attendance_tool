#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmjs.org}"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "需要 Apple Silicon macOS。" >&2
  exit 1
fi
for command in "$PYTHON" npm curl xcode-select; do
  command -v "$command" >/dev/null || { echo "缺少命令：$command" >&2; exit 1; }
done

if ! xcode-select -p >/dev/null 2>&1; then
  echo "请先运行 xcode-select --install 安装 Apple Command Line Tools。" >&2
  exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "正在安装 Rust 工具链……"
  export RUSTUP_DIST_SERVER="${RUSTUP_DIST_SERVER:-https://static.rust-lang.org}"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
fi
if [[ -f "$HOME/.cargo/env" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.cargo/env"
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  "$PYTHON" -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools
"$ROOT/.venv/bin/python" -m pip install --no-build-isolation -e "$ROOT[dev]"

export NPM_REGISTRY
"$ROOT/scripts/build_macos_dmg.sh"
