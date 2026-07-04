"""CI manifest↔code consistency check. Spec §2.3 / 门0."""
from dataclasses import dataclass
from manifest_loader import StrategyManifest


@dataclass
class LintResult:
    ok: bool
    errors: list


def lint_manifest(manifest: StrategyManifest, code_params: set, state_fields: set) -> LintResult:
    errors = []
    for p in manifest.parameter_space:
        if p.name not in code_params:
            errors.append(f"parameter '{p.name}' declared in manifest but not found in strategy code")
    manifest_fields = {f.name for f in manifest.state_schema.fields}
    for f in manifest_fields - state_fields:
        errors.append(f"state field '{f}' declared in manifest but not produced by SerializeRlState()")
    for f in state_fields - manifest_fields:
        errors.append(f"state field '{f}' produced by SerializeRlState() but not in manifest")
    return LintResult(ok=len(errors) == 0, errors=errors)
