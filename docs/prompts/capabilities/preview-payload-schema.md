# Capability: Preview Payload Schema

The hidden compiler must emit only the currently supported preview payload schema.

## Top-Level Fields
- `goal` (required semantic anchor)
- `title` (optional metadata)
- `enqueue_child` (optional)
- `write_file` (optional)
- `file_organize` (optional — bounded copy/move/archive workflows)
- `steps` (optional — direct bounded file skill actions)

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

## `file_organize` Structure
- `source_path` (required — must be within ~/VoxeraOS/notes/ scope)
- `destination_dir` (required — must be within ~/VoxeraOS/notes/ scope)
- `mode` (`copy` | `move`)
- `overwrite` (boolean, default false)
- `delete_original` (boolean, default false)

Use `file_organize` for bounded copy, move, archive, and organize workflows.
Paths outside ~/VoxeraOS/notes/ or within ~/VoxeraOS/notes/queue/ are rejected.

## `steps` Structure
Array of objects, each with:
- `skill_id` (required — bounded file skill id like `files.exists`, `files.stat`, `files.read_text`, `files.mkdir`, `files.delete_file`)
- `args` (object)

Use `steps` for direct single-skill bounded file actions when the intent maps cleanly
to one skill invocation (e.g. existence check, file stat, read, mkdir, delete).

## Workspace-Root-Relative Path Shorthand
A leading `/` in a bounded file path is interpreted as workspace-root-relative, not host absolute:
- `/foo/bar.txt` resolves to `~/VoxeraOS/notes/foo/bar.txt`
- `/queue/health.json` is still rejected (queue control-plane)
- Parent traversal (`..`) is still rejected

## Truth Boundary
Preview payloads are authoritative only before submit (preview truth).
Queue/runtime truth begins only after handoff is accepted by queue state.


## Decision envelope constraint
Hidden compiler output must remain strict JSON decision envelopes only (`replace_preview`, `patch_preview`, `no_change`) with valid preview payload objects where applicable.
