# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in VoxeraOS, please report it responsibly.

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, email security concerns to the maintainer via the contact information in the repository, or use [GitHub's private vulnerability reporting](https://github.com/SeedOfEvil/VoxeraOS/security/advisories/new) if available.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

## What to expect

This is a one-person alpha project. Response times may vary, but all security reports will be taken seriously and addressed as quickly as possible.

## Scope

VoxeraOS is alpha software and is not intended for production use. That said, the project takes security seriously as a core design principle:

- All real-world side effects are capability-gated and policy-evaluated
- Fail-closed is the default behavior when uncertain
- A red-team regression suite (`make security-check`) runs as part of the merge gate
- Prompt injection defenses, traversal hardening, and approval integrity are actively maintained

For the full security posture and threat model, see [docs/SECURITY.md](docs/SECURITY.md).

## Supported versions

Only the latest alpha release is actively maintained. There are no backported security fixes for older versions at this stage of the project.

| Version | Supported |
|---------|-----------|
| 0.1.x (latest) | Yes |
| Older | No |
