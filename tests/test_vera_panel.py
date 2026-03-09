from __future__ import annotations

from voxera.panel import app as panel_module


def test_panel_does_not_host_vera_routes():
    paths = {route.path for route in panel_module.app.routes if getattr(route, "path", "")}
    assert "/vera" not in paths
    assert "/vera/chat" not in paths
