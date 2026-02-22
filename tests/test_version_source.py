from importlib.metadata import PackageNotFoundError

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
