"""Commercial-Safe Code & Dependency License Preflight Auditor (Clean-Room).

Scans repository Python source files and installed distribution metadata (`importlib.metadata`)
to flag commercial-unsafe dependencies (AGPL-3.0, BUSL-1.1, CC-BY-NC / Non-Commercial).
100% Python stdlib, MIT-licensed.
"""

from __future__ import annotations

import importlib.metadata as ilm
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

BANNED_CODE_PATTERNS: list[tuple[str, str, str]] = [
    (r"\bimport\s+depthflow\b|\bfrom\s+depthflow\b", "DepthFlow", "AGPL-3.0 (Code)"),
    (r"\bimport\s+insightface\b|\bfrom\s+insightface\b|buffalo_l", "InsightFace / buffalo_l", "Non-Commercial (Weights)"),
    (r"depth_anything_v2_vit[bl]\b|Depth-Anything-V2-(Base|Large)", "Depth Anything V2 Base/Large", "CC-BY-NC-4.0 (Weights)"),
    (r"\brtk_engine\b|\brtk_proxy\b", "rtk standard edition", "BUSL-1.1 (Code)"),
]

BANNED_DIST_NAMES = {"depthflow", "insightface", "rtk-engine", "rtk-proxy"}
BANNED_LICENSE_TOKENS = ("agpl", "busl-1.1", "cc-by-nc", "non-commercial", "noncommercial")
SCAN_DIRS = ("core", "mcp", "scripts", "examples")


@dataclass
class LicenseFinding:
    severity: str  # "FAIL" | "WARN"
    category: str  # "code_import" | "weights_ref" | "installed_pkg" | "registry"
    component: str
    license_issue: str
    location: str


@dataclass
class LicenseAuditReport:
    passed: bool
    scanned_files: int
    findings: list[LicenseFinding] = field(default_factory=list)
    registry_present: bool = False

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "scanned_files": self.scanned_files,
            "registry_present": self.registry_present,
            "findings": [asdict(f) for f in self.findings],
        }


def run_license_audit(
    project_root: Optional[Path] = None,
    check_installed_pkgs: bool = True,
    require_model_registry: bool = False,
) -> LicenseAuditReport:
    root = project_root or Path(__file__).resolve().parent.parent
    findings: list[LicenseFinding] = []
    registry_path = root / "MODEL_LICENSES.md"
    registry_present = registry_path.exists()
    if require_model_registry and not registry_present:
        findings.append(
            LicenseFinding(
                severity="WARN",
                category="registry",
                component="MODEL_LICENSES.md",
                license_issue="Missing optional Code vs. Model Weights license registry file",
                location=str(registry_path),
            )
        )

    files_to_scan: list[Path] = []
    for d in SCAN_DIRS:
        dp = root / d
        if dp.exists():
            for fp in sorted(dp.rglob("*.py")):
                if fp.name == "license_audit.py":
                    continue
                files_to_scan.append(fp)

    for fp in files_to_scan:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat, comp, lic in BANNED_CODE_PATTERNS:
                if re.search(pat, stripped, flags=re.IGNORECASE):
                    if any(
                        tok in stripped.lower()
                        for tok in ("banned", "forbidden", "raise ", "never use", "no insightface", "zero insightface")
                    ):
                        continue
                    cat = "weights_ref" if "Weights" in lic else "code_import"
                    findings.append(
                        LicenseFinding(
                            severity="FAIL",
                            category=cat,
                            component=comp,
                            license_issue=lic,
                            location=f"{fp.relative_to(root)}:{lineno}",
                        )
                    )

    if check_installed_pkgs:
        for dist in ilm.distributions():
            name = (dist.metadata.get("Name") or "").strip().lower()
            lic_str = (dist.metadata.get("License") or "").strip().lower()
            if name in BANNED_DIST_NAMES:
                findings.append(
                    LicenseFinding(
                        severity="FAIL",
                        category="installed_pkg",
                        component=name,
                        license_issue=f"Banned package installed ({lic_str or 'restricted'})",
                        location="site-packages",
                    )
                )
            elif any(tok in lic_str for tok in BANNED_LICENSE_TOKENS) and name not in {"scipy"}:
                findings.append(
                    LicenseFinding(
                        severity="WARN",
                        category="installed_pkg",
                        component=name,
                        license_issue=f"Restricted license metadata: {lic_str[:60]}",
                        location="site-packages",
                    )
                )

    passed = not any(f.severity == "FAIL" for f in findings)
    return LicenseAuditReport(
        passed=passed,
        scanned_files=len(files_to_scan),
        findings=findings,
        registry_present=registry_present,
    )


def main() -> None:
    rep = run_license_audit()
    print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    if not rep.passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
