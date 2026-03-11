from __future__ import annotations

from voxera import secrets as secrets_module


def test_write_get_unset_secret_file_fallback(tmp_path, monkeypatch):
    fallback = tmp_path / "secrets.env"
    monkeypatch.setattr(secrets_module, "_fallback_path", lambda: fallback)

    class _KeyringFail:
        @staticmethod
        def set_password(*_args, **_kwargs):
            raise RuntimeError("no keyring")

        @staticmethod
        def get_password(*_args, **_kwargs):
            raise RuntimeError("no keyring")

        @staticmethod
        def delete_password(*_args, **_kwargs):
            raise RuntimeError("no keyring")

    monkeypatch.setattr(secrets_module, "keyring", _KeyringFail)

    source = secrets_module.write_secret("BRAVE_API_KEY", "value-1")
    assert source == "file:BRAVE_API_KEY"
    assert secrets_module.get_secret("BRAVE_API_KEY") == "value-1"
    assert secrets_module.unset_secret("BRAVE_API_KEY") is True
    assert secrets_module.get_secret("BRAVE_API_KEY") is None
    assert not fallback.exists()


def test_unset_secret_returns_false_when_missing(tmp_path, monkeypatch):
    fallback = tmp_path / "secrets.env"
    monkeypatch.setattr(secrets_module, "_fallback_path", lambda: fallback)

    class _KeyringNoEntry:
        @staticmethod
        def get_password(*_args, **_kwargs):
            return None

        @staticmethod
        def delete_password(*_args, **_kwargs):
            return None

    monkeypatch.setattr(secrets_module, "keyring", _KeyringNoEntry)

    assert secrets_module.unset_secret("OPENROUTER_API_KEY") is False
