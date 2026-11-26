# Security Policy

## Core Security Guarantees
TierForge Harness is designed as a **localhost-only, zero OS-mutation** developer harness:
- **Zero OS Mutations:** Never modifies Windows Registry, OS services, network/IPv6 stacks, or system pagefiles.
- **Localhost Isolation:** All local bridges and Ollama endpoints bind strictly to `127.0.0.1`.
- **Clean-Room Dependencies:** `core/` uses 100% Python Standard Library (zero third-party runtime packages).
- **Browser Extension Hardening:** See [extensions/tierforge-bridge/SECURITY.md](extensions/tierforge-bridge/SECURITY.md) for TOFU Origin Pinning, scoped `host_permissions`, and 1-click kill switch details.

## Supported Versions

| Version | Supported |
| :--- | :---: |
| `0.1.x` | Yes |

## Reporting a Vulnerability
If you discover a security issue (e.g., command injection, path traversal, localhost bridge auth bypass, or license violation), please **do not** open a public issue with exploit details.

1. Use **GitHub Security Advisories** ("Report a vulnerability" in the repository's **Security** tab), or contact the maintainers directly via the repository profile.
2. Include steps to reproduce, affected OS/Python version, and potential impact.
3. We aim to acknowledge reports within 48 hours and publish a fix promptly.
