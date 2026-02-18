from __future__ import annotations


def run(
    command: str | list[str],
    timeout_s: int = 60,
    env: dict[str, str] | None = None,
    network: bool = False,
):
    return {
        "command": command,
        "timeout_s": timeout_s,
        "env": env or {},
        "network": network,
    }
