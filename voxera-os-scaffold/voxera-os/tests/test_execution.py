from voxera.skills.execution import PodmanSandboxRunner


def test_parse_network_setting_accepts_bool_and_string_values():
    assert PodmanSandboxRunner._parse_network_setting(True) is True
    assert PodmanSandboxRunner._parse_network_setting(False) is False
    assert PodmanSandboxRunner._parse_network_setting("true") is True
    assert PodmanSandboxRunner._parse_network_setting("TRUE") is True
    assert PodmanSandboxRunner._parse_network_setting("1") is True
    assert PodmanSandboxRunner._parse_network_setting("false") is False
    assert PodmanSandboxRunner._parse_network_setting("0") is False
    assert PodmanSandboxRunner._parse_network_setting(None) is False


def test_parse_network_setting_rejects_non_boolean_like_values():
    try:
        PodmanSandboxRunner._parse_network_setting("maybe")
    except ValueError as exc:
        assert str(exc) == "network must be a boolean value"
    else:
        raise AssertionError("expected ValueError")

    try:
        PodmanSandboxRunner._parse_network_setting(1)
    except ValueError as exc:
        assert str(exc) == "network must be a boolean value"
    else:
        raise AssertionError("expected ValueError")
