# Bootstrap (First-run Setup)

Voxera must start with a **typed setup wizard** because voice may not be available at first boot.

## Setup goals
- Choose interaction mode (voice/GUI/CLI/mixed)
- Choose brain source (local vs cloud)
- Configure provider endpoint + model
- Store secrets safely (keyring preferred)
- Run capability test suite
- Write config + policy pack

## Command
```bash
voxera setup
```

## Output files
- Config: `~/.config/voxera/config.yml`
- Policy: `~/.config/voxera/policy.yml`
- Capability report: `~/.local/share/voxera/capabilities.json`
- Audit: `~/.local/share/voxera/audit/*.jsonl`
