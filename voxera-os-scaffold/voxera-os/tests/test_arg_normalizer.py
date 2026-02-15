from voxera.skills.arg_normalizer import canonicalize_args


def test_open_app_canonicalizes_aliases():
    out = canonicalize_args("system.open_app", {"name": "Firefox"})
    assert out["name"] == "firefox"

    out = canonicalize_args("system.open_app", {"name": "terminal"})
    assert out["name"] == "gnome-terminal"


def test_set_volume_clamps_and_coerces():
    out = canonicalize_args("system.set_volume", {"percent": "140"})
    assert out["percent"] == "100"

    out = canonicalize_args("system.set_volume", {"percent": -5})
    assert out["percent"] == "0"
