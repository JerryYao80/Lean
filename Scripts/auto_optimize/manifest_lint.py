"""CI manifest↔code consistency check. Spec §2.3 / 门0."""
from dataclasses import dataclass, field
from manifest_loader import StrategyManifest


@dataclass
class LintResult:
    ok: bool
    errors: list
    warnings: list = field(default_factory=list)   # Spec §5.4: advisory, non-gate-failing


def lint_manifest(manifest: StrategyManifest, code_params: set, state_fields: set) -> LintResult:
    errors = []
    warnings = []
    completeness = getattr(manifest, "rl_state_completeness", "unaudited")
    if completeness in ("partial", "unaudited"):
        errors.append(
            f"rl_state_completeness='{completeness}' — 策略不允许进入灰度部署 "
            f"(state 字段未全部填真实值或未审计); 请补全 SerializeRlState 真实字段后改 'full'")
    for p in manifest.parameter_space:
        if p.name not in code_params:
            errors.append(f"parameter '{p.name}' declared in manifest but not found in strategy code")
    manifest_fields = {f.name for f in manifest.state_schema.fields}
    for f in manifest_fields - state_fields:
        errors.append(f"state field '{f}' declared in manifest but not produced by SerializeRlState()")
    for f in state_fields - manifest_fields:
        errors.append(f"state field '{f}' produced by SerializeRlState() but not in manifest")
    # Spec §5.4: review-not-configured advisory warning (not an error → ok stays derivable from errors)
    raw = getattr(manifest, "raw", None) or {}
    review = raw.get("review", {})
    if not review:
        warnings.append("no review: block in manifest — trading-review (复盘) not configured")
    return LintResult(ok=len(errors) == 0, errors=errors, warnings=warnings)
