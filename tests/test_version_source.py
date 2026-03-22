from importlib.metadata import PackageNotFoundError
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from voxera import __version__
from voxera.version import _version_from_pyproject, get_version


def test_dunder_version_matches_shared_source():
    assert __version__ == get_version()


def test_get_version_falls_back_to_pyproject(monkeypatch):
    monkeypatch.setattr(
        "voxera.version.package_version",
        lambda _: (_ for _ in ()).throw(PackageNotFoundError()),
    )

    assert get_version() == _version_from_pyproject()


def test_pyproject_declares_tomli_for_python_lt_311():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    deps = payload["project"]["dependencies"]

    assert any(dep.startswith("tomli>=2.0") and "python_version < '3.11'" in dep for dep in deps)


def test_project_version_truth_is_0_1_8_and_documented():
    repo_root = Path(__file__).resolve().parents[1]
    pyproject_payload = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    readme = (repo_root / "README.md").read_text(encoding="utf-8")

    assert pyproject_payload["project"]["version"] == "0.1.8"
    assert "Alpha (v0.1.8)" in readme
