# TierForge Harness — Legal, Licensing & Commercial Compliance

## 1. Open-Source License Hygiene (100% Commercial-Safe)
- **Core (`core/`):** 100% Python Standard Library (`stdlib_pure = true`), zero third-party runtime dependencies.
- **Strictly Banned Dependencies:** Automated `core/license_audit.py` blocks any AGPL, CC-BY-NC, or BUSL-1.1 packages (`DepthFlow`, `InsightFace`/`buffalo_l`, `Depth Anything V2 Base/Large`, `rtk`).
- **Automated Enforcement:** Every step/release is gated by `scripts/audit_step.py` (`license_passed = true` required).

## 2. Web AI Platform Terms & BYOA (Bring Your Own Account)
- **User-Owned Sessions Only:** TierForge is a **local productivity harness**. Users authenticate with their own accounts on their own machines. TierForge **never** resells API access, pools/shares accounts, or distributes credentials.
- **No Anti-Bot / CAPTCHA Bypass:** TierForge strictly prohibits bypassing CAPTCHAs, proof-of-work challenges, rate limits, or access controls. If a platform presents a verification prompt, execution pauses for the user.
- **Respect Platform Limits:** Users remain responsible for complying with the Terms of Service of the third-party AI services they connect to (Google Gemini, OpenAI ChatGPT, Anthropic Claude, etc.). For high-volume commercial workloads, official paid API keys are recommended via Tier-0/API adapters.

## 3. Browser Extension Store Compliance (Chrome Web Store / Edge Add-ons / Firefox AMO)
- **Single Purpose:** Connects the user's local TierForge harness (`127.0.0.1:19876`) to the user's own AI web tabs.
- **Data Privacy (Zero Data Collected):**
  - Zero cloud servers, zero analytics, zero third-party telemetry.
  - Strictly scoped `host_permissions`: `http://127.0.0.1:19876/*` and the 4 supported AI web apps only (never `<all_urls>`, `cookies`, `history`, or `webRequest`).
- **Security Controls:**
  - Localhost-only binding (`127.0.0.1`).
  - TOFU (Trust-On-First-Use) Origin Pinning blocks websites and rogue extensions with `HTTP 403`.
  - Visible `RUN` badge, local Activity Log, and 1-click **Pause Bridge** kill switch.
  - 100% unminified, human-auditable JavaScript source.
