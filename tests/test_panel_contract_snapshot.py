from __future__ import annotations

from voxera.panel import app as panel_module


def test_panel_public_route_surface_snapshot():
    paths = sorted(
        route.path for route in panel_module.app.routes if getattr(route, "path", None)
    )

    expected = sorted(
        [
            "/",
            "/assistant",
            "/assistant/ask",
            "/bundle/system",
            "/docs",
            "/docs/oauth2-redirect",
            "/hygiene",
            "/hygiene/health-reset",
            "/hygiene/prune-dry-run",
            "/hygiene/reconcile",
            "/jobs",
            "/jobs/{job_id}",
            "/jobs/{job_id}/bundle",
            "/missions/create",
            "/missions/create",
            "/missions/templates/create",
            "/missions/templates/create",
            "/openapi.json",
            "/queue/approvals/{ref}/approve",
            "/queue/approvals/{ref}/approve-always",
            "/queue/approvals/{ref}/deny",
            "/queue/create",
            "/queue/create",
            "/queue/jobs/{job}/detail",
            "/queue/jobs/{ref}/cancel",
            "/queue/jobs/{ref}/delete",
            "/queue/jobs/{ref}/retry",
            "/queue/pause",
            "/queue/resume",
            "/recovery",
            "/recovery/download/{bucket}/{name}",
            "/redoc",
            "/static",
        ]
    )

    assert paths == expected
