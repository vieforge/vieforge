# VieForge Enterprise & Commercialization Architecture

VieForge follows a clean, transparent **Open-Core** model designed for individual developers, scaling engineering teams, and regulated enterprises.

## 1. Open-Core Packaging Tiers

| Capability | **MIT Core (Free Forever)** | **Pro / Team (Commercial Add-On)** | **Enterprise (Self-Hosted / Air-Gapped)** |
| :--- | :---: | :---: | :---: |
| 3-Tier Orchestration (`core/` 100% Python stdlib) | Included | Included | Included |
| SQLite WAL Checkpoints, SHA-256 Dedup & `$0` Resume | Included | Included | Included |
| Local LLM Router (Ollama + OpenAI-compat + SQLite Cache) | Included | Included | Included |
| Output Trimmer + Automatic Secret Redaction (`redact_secrets=True`) | Included | Included | Included |
| MCP Worker Server (`mcp 2.x` & `1.x`) + Multi-IDE Skill Installer | Included | Included | Included |
| Non-Root Docker Container (`Dockerfile`, `compose.yml`) | Included | Included | Included |
| OpenTelemetry `gen_ai.*` JSONL Tracing (`emit_genai_span`) | Included | OTLP / Langfuse / Phoenix Push | Dedicated SIEM / SOC Sink |
| Pluggable Local RBAC & Audit Log (`core/policy_guard.py`) | Local Hook | Team Approval Webhooks | SAML / OIDC SSO + OPA Policies |
| Multi-Node LAN / GPU Worker Pool & Spend Dashboard UI | — | Planned | Planned |
| Commercial SLA, Compliance Packs & Priority Support | — | Email Support | Dedicated SLA (`hello@vieforge.dev`) |

## 2. Security & Compliance Posture

- **Zero OS-Mutation Guarantee:** Never modifies Windows Registry, system services, network stacks, or pagefiles.
- **Supply-Chain Provenance:** Published to PyPI via **OIDC Trusted Publishing** with **PEP 740 / SLSA Build Provenance Attestations** and automated Clean-Room License Auditing (`python -m core.license_audit`, blocking `AGPL-3.0`, `BUSL-1.1`, `CC-BY-NC`).
- **Data Residency & Air-Gapped Operation:** Runs 100% locally with Ollama, LM Studio, llama.cpp, or vLLM; secret redaction (`sk-`, `ghp_`, `AIza`, `AKIA`, `Bearer`, env secrets) strips credentials before any summary reaches Tier 1 Cloud Agents.
- **Non-Root Containerization:** Ships with a read-only-compatible, unprivileged `Dockerfile` and `compose.yml` (`vieforge` system user + built-in `vieforge doctor --json` healthcheck).

## 3. Commercial & Enterprise Inquiries

- **General & Licensing:** `hello@vieforge.dev`
- **Security Advisories:** `security@vieforge.dev` (or GitHub Private Security Advisories)
- **Website:** https://vieforge.dev
