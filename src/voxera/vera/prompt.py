from __future__ import annotations

from ..prompts import compose_hidden_compiler_prompt, compose_vera_prompt

VERA_SYSTEM_PROMPT = compose_vera_prompt()


def vera_queue_boundary_summary() -> str:
    return (
        "Queue boundary: Vera can reason, draft, and submit explicit handoffs into the VoxeraOS queue; "
        "VoxeraOS owns planning, policy/approval checks, execution, and evidence. Submission is not execution."
    )


VERA_PREVIEW_BUILDER_PROMPT = compose_hidden_compiler_prompt()
