from __future__ import annotations

from pathlib import Path

import pytest

from voxera import golden_surfaces


def test_normalize_help_text_rewrites_root_and_trims_padding() -> None:
    raw = "\n\x1b[1m Usage: root queue [OPTIONS]\x1b[0m   \n"

    normalized = golden_surfaces.normalize_help_text(raw)

    assert normalized == "Usage: voxera queue [OPTIONS]\n"


def test_normalize_json_payload_masks_paths_and_timestamps(tmp_path: Path) -> None:
    payload = {
        "updated_at_ms": 1712345,
        "health_path": str(tmp_path / "queue" / "health.json"),
        "nested": {
            "last_shutdown_ts": 1719999,
            "queue_root": str(tmp_path / "queue"),
        },
    }

    normalized = golden_surfaces.normalize_json_payload(
        payload,
        tmp_prefixes=(str(tmp_path),),
        repo_root="/workspace/VoxeraOS",
        home="/root",
    )

    assert normalized["updated_at_ms"] == "<TS_MS>"
    assert normalized["health_path"] == "<TMP>/queue/health.json"
    assert normalized["nested"]["last_shutdown_ts"] == "<TS_MS>"
    assert normalized["nested"]["queue_root"] == "<TMP>/queue"


def test_check_golden_files_reports_drift(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir(parents=True)
    (golden_dir / "voxera_help.txt").write_text("expected\n", encoding="utf-8")

    surface = golden_surfaces.GoldenSurface(
        name="root-help",
        args=("--help",),
        file_name="voxera_help.txt",
        renderer="help",
    )

    monkeypatch.setattr(golden_surfaces, "SURFACES", (surface,))
    monkeypatch.setattr(golden_surfaces, "_golden_dir", lambda: golden_dir)
    monkeypatch.setattr(golden_surfaces, "_render_surface", lambda _surface: "actual\n")

    with pytest.raises(SystemExit, match="drift detected for root-help"):
        golden_surfaces.check_golden_files()
