# Security & Safety

## Threat model (MVP)
- Accidental destructive actions (rm, installs, firewall changes)
- Prompt injection (especially when reading files/web later)
- Secret leakage (API keys, tokens)
- Over-permissioned tools

## Controls
- Skills declare required capabilities (network/install/files/etc.)
- Policy engine enforces allow/ask/deny per capability
- High-risk actions require explicit approval
- Secrets stored in keyring when possible (fallback file is 0600)
- Audit log records every action + output + rollback pointer
- Runner denies commands not in allowlist (MVP)

## Next hardening steps
- Container sandbox runner (Podman) + seccomp/apparmor profiles
- Signed skills + integrity verification
- Safe-mode boot (limited skills only)
- Redaction pipeline for logs & telemetry
