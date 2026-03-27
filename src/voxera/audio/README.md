# Audio stack (bounded foundation)

Voxera will support:
- Wake word (openWakeWord)
- STT (whisper.cpp or provider STT)
- TTS (Piper)

Current spike scope:
- Voice is feature-flagged and treated as an interface transport layer.
- Transcript-origin input is routed through standard Vera chat handling.
- Real side effects still require governed queue handoff.
- Output speech is placeholder-only metadata in this phase (no full speaking loop yet).
