from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path

SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


def _engine_version(path: Path) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise ValueError(f"未在 {path} 中找到 __version__")


def read_versions(root: Path) -> dict[str, str]:
    desktop_package = json.loads((root / "apps/desktop/package.json").read_text(encoding="utf-8"))
    tauri_config = json.loads(
        (root / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    cargo_config = tomllib.loads(
        (root / "apps/desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    )
    python_config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return {
        "apps/desktop/package.json": str(desktop_package["version"]),
        "apps/desktop/src-tauri/tauri.conf.json": str(tauri_config["version"]),
        "apps/desktop/src-tauri/Cargo.toml": str(cargo_config["package"]["version"]),
        "pyproject.toml": str(python_config["project"]["version"]),
        "attendance_engine/__init__.py": _engine_version(root / "attendance_engine/__init__.py"),
    }


def resolve_version(root: Path) -> str:
    versions = read_versions(root)
    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        details = "\n".join(f"- {path}: {version}" for path, version in versions.items())
        raise ValueError(f"项目版本号不一致：\n{details}")
    version = unique_versions.pop()
    if not SEMVER_PATTERN.fullmatch(version):
        raise ValueError(f"版本号不符合语义化版本格式：{version}")
    return version


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    try:
        print(resolve_version(root))
    except (KeyError, OSError, SyntaxError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
