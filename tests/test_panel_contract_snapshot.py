from __future__ import annotations

from voxera.panel import app as panel_module


def test_panel_public_route_surface_snapshot():
    route_contract = sorted(
        (
            route.path,
            tuple(sorted(route.methods)) if getattr(route, "methods", None) is not None else None,
        )
        for route in panel_module.app.routes
        if getattr(route, "path", None)
    )

    expected = sorted(
        [
            ("/", ("GET",)),
            ("/assistant", ("GET",)),
            ("/assistant/ask", ("POST",)),
            ("/assistant/progress/{request_id}", ("GET",)),
            ("/automations", ("GET",)),
            ("/automations/{automation_id}", ("GET",)),
            ("/automations/{automation_id}/delete", ("POST",)),
            ("/automations/{automation_id}/disable", ("POST",)),
            ("/automations/{automation_id}/enable", ("POST",)),
            ("/automations/{automation_id}/run-now", ("POST",)),
            ("/bundle/system", ("GET",)),
            ("/docs", ("GET", "HEAD")),
            ("/docs/oauth2-redirect", ("GET", "HEAD")),
            ("/hygiene", ("GET",)),
            ("/hygiene/health-reset", ("POST",)),
            ("/hygiene/prune-dry-run", ("POST",)),
            ("/hygiene/reconcile", ("POST",)),
            ("/jobs", ("GET",)),
            ("/jobs/{job_id}", ("GET",)),
            ("/jobs/{job_id}/bundle", ("GET",)),
            ("/jobs/{job_id}/progress", ("GET",)),
            ("/missions/create", ("GET",)),
            ("/missions/create", ("POST",)),
            ("/missions/templates/create", ("GET",)),
            ("/missions/templates/create", ("POST",)),
            ("/openapi.json", ("GET", "HEAD")),
            ("/queue/approvals/{ref}/approve", ("POST",)),
            ("/queue/approvals/{ref}/approve-always", ("POST",)),
            ("/queue/approvals/{ref}/deny", ("POST",)),
            ("/queue/create", ("GET",)),
            ("/queue/create", ("POST",)),
            ("/queue/jobs/{job}/detail", ("GET",)),
            ("/queue/jobs/{job}/progress", ("GET",)),
            ("/queue/jobs/{ref}/cancel", ("POST",)),
            ("/queue/jobs/{ref}/delete", ("POST",)),
            ("/queue/jobs/{ref}/retry", ("POST",)),
            ("/queue/pause", ("POST",)),
            ("/queue/resume", ("POST",)),
            ("/recovery", ("GET",)),
            ("/recovery/download/{bucket}/{name}", ("GET",)),
            ("/redoc", ("GET", "HEAD")),
            ("/static", None),
        ]
    )

    assert route_contract == expected
