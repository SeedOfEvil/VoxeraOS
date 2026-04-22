# Changelog

All notable changes to VoxeraOS will be documented in this file.

VoxeraOS is an open-source alpha project. APIs, CLI surfaces, and internal contracts may change between releases.

## [Unreleased]

### Added
- Voice-turn latency instrumentation + bounded reduction: `/chat/voice` payloads now carry a per-sub-stage breakdown under `stage_timings` (`vera_preview_builder_ms`, `vera_reply_ms`, `vera_enrichment_ms`) in addition to the existing `upload_ms / temp_write_ms / stt_ms / vera_ms / tts_ms / total_ms` umbrella keys. Sub-stage values are truthful: `None` when the branch did not run this turn, never fabricated. `ChatTurnResult` now exposes the same sub-stage dict so typed `/chat` surfaces can read it too.
- Voice-turn time-to-first-speech reduction: the preview builder and main Vera reply LLM calls now run concurrently via `asyncio.gather(...)` inside `run_vera_chat_turn`. On turns that previously paid for both serially this cuts the dominant `vera_ms` phase roughly in half, without changing preview truth, lifecycle semantics, or the text-is-authoritative contract (both calls still fully complete before preview writes and response text are finalized).
- Dictation UX: enhancer JS now shows a bounded "Synthesizing speech…" state between "Vera thinking…" and "Speaking reply…" when a spoken reply was requested, so a long TTS synthesis phase stops looking indistinguishable from a stalled Vera. The label is gated on `speakResponse` and clears the moment the server returns, never fabricating progress.
- `MoonshineLocalBackend` — optional local speech-to-text backend via the official `moonshine-voice` PyPI package, satisfying the existing canonical STT seam (file-oriented, lazy model load, truthful failure paths). Gated behind a new `[moonshine]` install extra.
  - Uses `moonshine_voice.Transcriber` (non-streaming) + the bundled pure-Python PCM WAV loader. Non-WAV inputs (browser `audio/webm`, etc.) are transparently transcoded to 16 kHz mono 16-bit PCM WAV via a narrow PyAV seam (`voice/audio_normalize.py`) before hitting Moonshine's loader; already-WAV files skip transcoding entirely.
  - Panel mic-capture JS prefers `audio/wav` directly via `MediaRecorder.isTypeSupported` when `moonshine_local` is the active backend; on browsers that decline the hint (currently most desktop Chrome/Edge/Firefox) the server-side transcode fallback handles it transparently.

### Fixed
- `[moonshine]` install extra now pulls `moonshine-voice>=0.0.5` (clean semver) instead of the broken `useful-moonshine-onnx>=0.2,<1.0` pin against a PyPI project that publishes only date-style versions (e.g. `20251121`). `pip install -e '.[dev,piper,whisper,kokoro,moonshine]'` now resolves cleanly.
- Operator-selectable STT backend: panel Voice Options now offers a bounded `whisper_local` / `moonshine_local` dropdown alongside the existing model selectors. Selection is persisted in runtime config (`voice_stt_backend`), invalid values fail truthfully, and an operator-selected Moonshine model id (`voice_stt_moonshine_model`) threads through the factory into the backend.
- Voice status summary now surfaces the effective STT backend and a `moonshine_model` sub-block (selected / effective) when Moonshine is configured; `voxera doctor --quick` reflects the effective backend and model.

### Changed
- `VOICE_STATUS_SUMMARY_SCHEMA_VERSION` bumped to 5 for the additive `moonshine_model` block.
- STT backend factory now exports a canonical `STT_BACKEND_CHOICES` tuple so the panel and other operator-facing surfaces share one source of truth.

## [0.1.9] — 2026-04-02

### Added
- Evidence-grounded review and follow-up workflows: Vera can now revise and save follow-ups from completed job evidence with distinct workflow paths.
- Live-path characterization tests for evidence-grounded review and follow-up workflows.
- Preview truth guardrail with sanitized_answer fallback for writing-draft turns.

### Changed
- Vera chat() orchestration decomposed: extracted draft content binding, response shaping, and early-exit intent handler dispatch into focused modules (app.py ~1864→~1182 lines, chat() ~1153→~445 lines).
- CLI queue extraction series completed: eight command-family modules extracted (files, health, hygiene, bundle, approvals, inbox, lifecycle, payloads); cli_queue.py reduced from ~909 to ~315 lines.
- Panel extraction: auth-state storage helpers and job-detail section assembly helpers extracted into dedicated modules.
- Governed capability expansion shipped: system inspection lanes, read-only URL investigation, richer file operations, capability semantics contracts, artifact/result shaping for operator evidence.
- Improved preview-only vs submitted reply UX with preview-state notices on writing-draft turns.
- Improved reliability of natural drafting and follow-up conversational paths.
- Polished linked-job review and evidence-grounded follow-up workflows.
- Version-consistency pass: bumped all authoritative and current-version surfaces to 0.1.9.

## [0.1.8] — 2026-03-22

### Changed
- Open-source readiness pass: README, docs, contributor/security surfaces aligned for public release.
- Simplified roadmap to broad directional milestones.
- Added CONTRIBUTING.md and root SECURITY.md.
- Version-consistency pass: bumped all authoritative and current-version surfaces to 0.1.8.
- Updated package metadata, README, ops.md, and version tests to reflect 0.1.8.

## [0.1.7] — 2026-03-10

### Added
- Productized onboarding flow: guided setup slots, OpenRouter live model picker, finish launch options.
- Curated vendor-grouped model catalog for OpenRouter brain configuration.
- Per-slot brain defaults (`primary`, `fast`, `reasoning`, `fallback`) with policy tier framing.

## [0.1.5]

### Added
- Hygiene/recovery baseline: `artifacts prune`, `queue prune`, `queue reconcile`, lock/shutdown hardening.

## [0.1.4]

### Added
- Stability and UX baseline: queue daemon, approvals, mission flows, panel/doctor foundations.
