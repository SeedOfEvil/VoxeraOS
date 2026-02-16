import os
import subprocess
from pathlib import Path


def test_e2e_smoke_script_exists_and_dry_mode_succeeds():
    script = Path(__file__).resolve().parents[1] / "scripts" / "e2e_smoke.sh"
    assert script.exists()

    env = dict(os.environ)
    env["E2E_DRY_RUN"] = "1"
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)

    assert proc.returncode == 0
    assert "dry mode enabled" in proc.stdout
