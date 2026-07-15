from pathlib import Path

import pytest

from scripts import resolve_app_version

ROOT = Path(__file__).resolve().parents[1]


def test_project_versions_are_consistent():
    versions = resolve_app_version.read_versions(ROOT)

    unique_versions = set(versions.values())
    assert len(unique_versions) == 1
    assert resolve_app_version.resolve_version(ROOT) == unique_versions.pop()


def test_version_resolver_rejects_mismatched_versions(monkeypatch):
    monkeypatch.setattr(
        resolve_app_version,
        "read_versions",
        lambda _root: {"package.json": "0.2.0", "Cargo.toml": "0.2.1"},
    )

    with pytest.raises(ValueError, match="版本号不一致"):
        resolve_app_version.resolve_version(ROOT)
