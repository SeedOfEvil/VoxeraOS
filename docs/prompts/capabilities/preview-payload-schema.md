# Capability: Preview Payload Schema

The hidden compiler must emit only the currently supported preview payload schema.

## Top-Level Fields
- `goal` (required semantic anchor)
- `title` (optional metadata)
- `enqueue_child` (optional)
- `write_file` (optional)

Do not invent unsupported top-level fields.

## `enqueue_child` Structure
- `goal` (required)
- `title` (optional)

Use `enqueue_child` only when child enqueue behavior is explicitly intended and supported.

## `write_file` Structure
- `path` (required)
- `content` (required)
- `mode` (optional)

Valid `mode` values:
- `overwrite`
- `append`

## Truth Boundary
Preview payloads are authoritative only before submit (preview truth).
Queue/runtime truth begins only after handoff is accepted by queue state.
