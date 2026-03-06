from __future__ import annotations

import typer

from .doctor import doctor_sync


def register(app: typer.Typer) -> None:
    @app.command()
    def doctor(
        self_test: bool = typer.Option(
            False, "--self-test", help="Run queue/audit/artifact golden-path self-test."
        ),
        quick: bool = typer.Option(False, "--quick", help="Run fast offline checks only."),
        timeout_s: float = typer.Option(
            8.0, "--timeout-s", min=1.0, help="Timeout for --self-test."
        ),
    ):
        """Run provider capability tests and write a report."""
        doctor_sync(self_test=self_test, timeout_s=timeout_s, quick=quick)
