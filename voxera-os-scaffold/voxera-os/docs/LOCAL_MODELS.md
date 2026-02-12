# Local model endpoints

Voxera supports **OpenAI-compatible** endpoints for maximum interoperability.

Common patterns:
- Ollama: http://localhost:11434/v1  (requires OpenAI-compat enabled in your setup)
- Other local gateways: provide /v1/chat/completions

Voxera can route tasks:
- Local lane: status, app launch, safe ops, offline
- Cloud lane (if enabled): codegen, deep troubleshooting, long planning
