from __future__ import annotations

import subprocess
from urllib.parse import urlparse

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run(url: str) -> RunResult:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return RunResult(
            ok=False,
            error="Only http/https URLs are allowed",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected non-http(s) URL",
                    machine_payload={"url": url, "scheme": parsed.scheme or None},
                    operator_note="Use an http:// or https:// URL.",
                    next_action_hint="provide_supported_url",
                    retryable=False,
                    error_class="invalid_input",
                )
            },
        )

    candidates = [
        ["firefox", "--new-tab", url],
        ["xdg-open", url],
    ]
    for cmd in candidates:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return RunResult(
                ok=True,
                output=f"Opened URL: {url}",
                data={
                    SKILL_RESULT_KEY: build_skill_result(
                        summary=f"Opened URL {url}",
                        machine_payload={"url": url, "launcher": cmd[0]},
                        operator_note="Browser launch requested successfully.",
                        next_action_hint="continue",
                        output_artifacts=[],
                    )
                },
            )
        except FileNotFoundError:
            continue
        except Exception as exc:
            return RunResult(
                ok=False,
                error=repr(exc),
                data={
                    SKILL_RESULT_KEY: build_skill_result(
                        summary="Failed to open URL",
                        machine_payload={"url": url, "launcher": cmd[0], "exception": repr(exc)},
                        operator_note="URL launch failed unexpectedly.",
                        next_action_hint="inspect_launcher",
                        retryable=True,
                        error_class="launcher_error",
                    )
                },
            )

    return RunResult(
        ok=False,
        error="No browser launcher found (firefox/xdg-open)",
        data={
            SKILL_RESULT_KEY: build_skill_result(
                summary="No supported browser launcher found",
                machine_payload={"url": url, "candidates": ["firefox", "xdg-open"]},
                operator_note="Install firefox or xdg-open to enable URL launching.",
                next_action_hint="install_launcher",
                retryable=False,
                error_class="missing_dependency",
            )
        },
    )
