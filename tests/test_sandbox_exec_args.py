"""Unit tests for canonicalize_argv — the single source of truth for
sandbox.exec command normalisation.

Covers the four regression patterns from the intermittent
'non-empty list of strings' failures:
  1) accepts string command and tokenises via shlex.split
  2) accepts argv / cmd aliases
  3) rejects empty argv after normalisation
  4) rejects non-string tokens
"""

from __future__ import annotations

import pytest

from voxera.skills.arg_normalizer import canonicalize_argv

# ---------------------------------------------------------------------------
# 1) String command — tokenised via shlex.split (posix=True, the default)
# ---------------------------------------------------------------------------


def test_canonicalize_argv_simple_string():
    assert canonicalize_argv({"command": "echo hello"}) == ["echo", "hello"]


def test_canonicalize_argv_string_with_single_quotes():
    """Quoted tokens are shell-unquoted by shlex.split."""
    assert canonicalize_argv({"command": "echo 'hello world'"}) == ["echo", "hello world"]


def test_canonicalize_argv_string_with_double_quotes():
    assert canonicalize_argv({"command": 'printf "%s" hi'}) == ["printf", "%s", "hi"]


def test_canonicalize_argv_string_bash_lc_form():
    """A typical planner-produced string is tokenised correctly."""
    assert canonicalize_argv({"command": "bash -lc 'echo hi'"}) == [
        "bash",
        "-lc",
        "echo hi",
    ]


def test_canonicalize_argv_string_strips_surrounding_whitespace():
    assert canonicalize_argv({"command": "  echo hi  "}) == ["echo", "hi"]


# ---------------------------------------------------------------------------
# 2) Key aliases: argv and cmd
# ---------------------------------------------------------------------------


def test_canonicalize_argv_argv_alias_list():
    assert canonicalize_argv({"argv": ["ip", "a"]}) == ["ip", "a"]


def test_canonicalize_argv_cmd_alias_list():
    assert canonicalize_argv({"cmd": ["ls", "-la"]}) == ["ls", "-la"]


def test_canonicalize_argv_argv_alias_string():
    assert canonicalize_argv({"argv": "echo hi"}) == ["echo", "hi"]


def test_canonicalize_argv_cmd_alias_string():
    assert canonicalize_argv({"cmd": "echo hi"}) == ["echo", "hi"]


def test_canonicalize_argv_command_priority_over_argv():
    """'command' wins when both 'command' and 'argv' are present."""
    result = canonicalize_argv({"command": ["echo", "cmd"], "argv": ["echo", "argv"]})
    assert result == ["echo", "cmd"]


def test_canonicalize_argv_argv_priority_over_cmd():
    """'argv' wins over 'cmd' when 'command' is absent."""
    result = canonicalize_argv({"argv": ["echo", "argv"], "cmd": ["echo", "cmd"]})
    assert result == ["echo", "argv"]


def test_canonicalize_argv_list_passthrough():
    argv = canonicalize_argv({"command": ["bash", "-lc", "echo hi"]})
    assert argv == ["bash", "-lc", "echo hi"]


# ---------------------------------------------------------------------------
# 3) Empty argv after normalisation → ValueError
# ---------------------------------------------------------------------------


def test_canonicalize_argv_empty_string_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": ""})


def test_canonicalize_argv_whitespace_only_string_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": "   "})


def test_canonicalize_argv_newline_tab_string_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": "\n\t"})


def test_canonicalize_argv_empty_list_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": []})


def test_canonicalize_argv_all_empty_tokens_raises():
    """After stripping, if no tokens remain, raise."""
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": ["", " ", "\t"]})


def test_canonicalize_argv_no_recognised_key_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"timeout_s": 60})


def test_canonicalize_argv_empty_dict_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({})


# ---------------------------------------------------------------------------
# 4) Non-string tokens → ValueError
# ---------------------------------------------------------------------------


def test_canonicalize_argv_int_token_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": ["echo", 1]})


def test_canonicalize_argv_none_token_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": ["echo", None]})


def test_canonicalize_argv_mixed_types_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": [1, "echo"]})


# ---------------------------------------------------------------------------
# Unsupported value types
# ---------------------------------------------------------------------------


def test_canonicalize_argv_int_value_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": 123})


def test_canonicalize_argv_dict_value_raises():
    """A nested dict passed as the command value is not accepted."""
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": {"cmd": "echo hi"}})


def test_canonicalize_argv_none_value_raises():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": None})


# ---------------------------------------------------------------------------
# Empty-token hardening
# ---------------------------------------------------------------------------


def test_canonicalize_argv_empty_tokens_rejected():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": ["", "ip", "", "a", ""]})


def test_canonicalize_argv_whitespace_tokens_rejected():
    with pytest.raises(ValueError, match="non-empty list of strings"):
        canonicalize_argv({"command": ["  ", "echo", " \t "]})


def test_canonicalize_argv_string_shell_control_rejected():
    with pytest.raises(ValueError, match="shell-control operators"):
        canonicalize_argv({"command": "echo hi && uname -a"})


def test_canonicalize_argv_inner_whitespace_preserved():
    """Whitespace inside a token is preserved."""
    assert canonicalize_argv({"command": ["echo", "hello world"]}) == [
        "echo",
        "hello world",
    ]


# ---------------------------------------------------------------------------
# Error message content
# ---------------------------------------------------------------------------


def test_canonicalize_argv_error_message_is_actionable():
    """Error message includes a concrete example for easy debugging."""
    with pytest.raises(ValueError) as exc_info:
        canonicalize_argv({"command": ""})
    msg = str(exc_info.value)
    assert "non-empty list of strings" in msg
    assert "bash" in msg  # example command in message
