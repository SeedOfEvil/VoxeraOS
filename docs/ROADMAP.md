# Roadmap

## Alpha v0.1.3 (current)
- Setup wizard (TUI)
- Provider abstraction (cloud/local)
- Skill runner + policy gate
- Minimal panel (approvals + audit)
- Audit logs (JSONL)
- Cloud-assisted mission planning (`voxera missions plan "<goal>"`)
- Queue reliability hardening:
  - schema-versioned failed sidecars (`failed/*.error.json`)
  - centralized schema-version policy (writer pin + reader allowlist) for sidecar validation
  - deterministic failed retention pruning (paired/orphan-aware, max-age/max-count)
  - failed status snapshots prefer valid sidecars while counting only primary jobs
  - queue failure lifecycle smoke coverage (fail -> snapshot -> prune)

## Alpha v0.2
- OpenAI-compatible provider solidified (Ollama, etc.)
- First 10 missions (work mode, status, volume, app launch, updates in ask-mode)
- Structured planning + dry-run simulation mode

## Alpha v0.3
- Voice stack: wake word + STT + TTS
- Voice-first command loop

## Alpha v0.4
- Sandbox runner (Podman)
- Signed skills + marketplace folder

## Beta v1.0
- ISO / image packaging
- Immutable base option + atomic updates
