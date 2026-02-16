from __future__ import annotations

from typing import Dict, List, Optional, Union


def run(
    command: Union[str, List[str]],
    timeout_s: int = 60,
    env: Optional[Dict[str, str]] = None,
    network: bool = False,
):
    return {
        "command": command,
        "timeout_s": timeout_s,
        "env": env or {},
        "network": network,
    }
