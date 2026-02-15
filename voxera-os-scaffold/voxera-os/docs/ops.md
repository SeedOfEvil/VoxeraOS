# Operations (Alpha)

This guide covers day-2 operations for VoxeraOS in service mode.

## Install services

From the Python project root:

```bash
cd ~/VoxeraOS/voxera-os-scaffold/voxera-os
make services-install
```

This installs user units from `deploy/systemd/user/` into `~/.config/systemd/user`, reloads
systemd user state, and enables/starts:
- `voxera-daemon.service`
- `voxera-panel.service`

## Update cadence

Recommended cadence: update frequently (daily/weekly) on active systems.

```bash
cd ~/VoxeraOS/voxera-os-scaffold/voxera-os
make update
```

`make update` runs the best-practice updater (`scripts/update.sh --smoke`), which:
- pulls latest `main`
- ensures `.venv` exists and reinstalls editable dependencies
- runs `compileall` + `pytest`
- runs E2E dry-run smoke checks
- restarts installed/enabled Voxera user services

## Rollback guidance (advanced)

If a fresh update causes regressions, rollback to a known commit:

```bash
cd ~/VoxeraOS
git log --oneline --decorate -n 20
git checkout <sha>
cd voxera-os-scaffold/voxera-os
bash scripts/update.sh --skip-tests --restart
```

Then validate:

```bash
voxera status
voxera queue status
```

When ready, return to main:

```bash
cd ~/VoxeraOS
git checkout main
git pull --ff-only
```

## Logs and audits

### Mission logs
- Mission summaries are appended to `~/VoxeraOS/notes/mission-log.md`.
- Queue and approval flows also leave traces in notes/audit artifacts.

### systemd journal

```bash
systemctl --user status voxera-daemon.service voxera-panel.service
journalctl --user -u voxera-daemon.service -f
journalctl --user -u voxera-panel.service -f
```

### Voxera audit views

```bash
voxera audit
voxera queue status
```

## Safety note

Operational workflows here do **not** require deleting data under `~/VoxeraOS/notes`.
