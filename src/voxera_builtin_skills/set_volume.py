from __future__ import annotations

import subprocess

from voxera.models import RunResult
from voxera.skills.result_contract import SKILL_RESULT_KEY, build_skill_result


def run(percent: str) -> RunResult:
    try:
        p = int(float(percent))
        p = min(100, max(0, p))
    except Exception:
        error = "percent must be numeric"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Rejected invalid volume input",
                    machine_payload={"percent": percent},
                    operator_note="Provide a numeric volume value between 0 and 100.",
                    next_action_hint="provide_numeric_percent",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="invalid_input",
                )
            },
        )

    try:
        subprocess.check_call(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{p}%"])
        return RunResult(
            ok=True,
            output=f"Volume set to {p}%",
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary=f"Set output volume to {p}%",
                    machine_payload={"percent": p},
                    operator_note="System volume update requested.",
                    next_action_hint="continue",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                )
            },
        )
    except FileNotFoundError:
        error = "pactl not found. Install pipewire-pulse or pulseaudio tools."
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Volume utility unavailable",
                    machine_payload={"launcher": "pactl"},
                    operator_note="Install pactl-compatible audio tooling.",
                    next_action_hint="install_audio_tooling",
                    retryable=False,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="missing_dependency",
                )
            },
        )
    except subprocess.CalledProcessError as e:
        error = f"pactl failed: {e}"
        return RunResult(
            ok=False,
            error=error,
            data={
                SKILL_RESULT_KEY: build_skill_result(
                    summary="Failed to set system volume",
                    machine_payload={"percent": p},
                    operator_note="Audio backend rejected the volume change.",
                    next_action_hint="inspect_audio_backend",
                    retryable=True,
                    blocked=False,
                    approval_status="none",
                    error=error,
                    error_class="audio_command_failed",
                )
            },
        )
