"""Preregistration + candidate-event JSON schema loaders and validators for
the Gold2 closed-loop economic proof (Task 9, Phase 2 STOP gate).

Loads Draft 2020-12 schemas from
``Scripts/gold2_closed_loop/schema/*.schema.json`` and enforces:

* the closed verdict-disposition pairing rule from design §5 line 198;
* every numeric threshold listed in design §13;
* finite-value rejection (NaN / +Inf / -Inf) via a python post-check,
  because ``jsonschema`` alone accepts ``Infinity`` and ``NaN`` as valid
  ``type: number`` instances (per the JSON schema spec, which defers to
  ``json.loads``).

No production code (the mature Gold2 strategy, the permissive RL trace
loader, ``evolution_scheduler.py``) is modified.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from Scripts.gold2_closed_loop.state_machine import (
    Disposition,
    HistoricalVerdict,
)

_SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


def _load_schema(name: str) -> dict[str, Any]:
    path = _SCHEMA_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"schema file not found: {path}")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"schema file {path} did not contain a JSON object")
    return loaded


# Compile validators once at import time so repeated validation is cheap.
_PREREGISTRATION_SCHEMA = _load_schema("preregistration.schema.json")
_CANDIDATE_EVENT_SCHEMA = _load_schema("candidate-event.schema.json")
_PREREGISTRATION_VALIDATOR = Draft202012Validator(_PREREGISTRATION_SCHEMA)
_CANDIDATE_EVENT_VALIDATOR = Draft202012Validator(_CANDIDATE_EVENT_SCHEMA)


# --- verdict / disposition pairing (design §5 line 198) ------------


def validate_verdict(
    historical_verdict: HistoricalVerdict | str,
    disposition: Disposition | str | None,
) -> bool:
    """Validate the closed verdict-disposition pairing (design §5 line 198).

    Legal pairings:
      * ``NOT_EVALUATED``  <-> ``UNPROVEN``
      * ``NOT_EFFECTIVE``  <-> ``REFUTED``
      * ``EFFECTIVE``      <-> ``None``
      * ``PARTIALLY_EFFECTIVE`` <-> ``None``

    Returns ``True`` if the pair is legal, raises ``ValueError`` otherwise.

    Parameters
    ----------
    historical_verdict:
        Verdict enum member or string value.
    disposition:
        Disposition enum member, string value, or ``None``. ``None`` is
        represented as the python ``None`` (there is no ``Disposition.NULL``
        member, intentionally — the type is ``Disposition | None``).
    """
    verdict_str = _coerce_verdict(historical_verdict)
    disp_str: str | None
    if disposition is None:
        disp_str = None
    elif isinstance(disposition, Disposition):
        disp_str = disposition.value
    elif isinstance(disposition, str):
        try:
            disp_str = Disposition(disposition).value
        except ValueError as error:
            raise ValueError(
                f"unknown disposition {disposition!r}; "
                f"expected one of {[member.value for member in Disposition]} or None"
            ) from error
    else:
        raise TypeError(
            f"disposition must be a Disposition | str | None, "
            f"got {type(disposition).__name__}"
        )

    if verdict_str == HistoricalVerdict.NOT_EVALUATED.value:
        if disp_str == Disposition.UNPROVEN.value:
            return True
        raise ValueError(
            f"verdict {verdict_str} pairs only with UNPROVEN; got {disp_str!r}"
        )
    if verdict_str == HistoricalVerdict.NOT_EFFECTIVE.value:
        if disp_str == Disposition.REFUTED.value:
            return True
        raise ValueError(
            f"verdict {verdict_str} pairs only with REFUTED; got {disp_str!r}"
        )
    if verdict_str in (
        HistoricalVerdict.EFFECTIVE.value,
        HistoricalVerdict.PARTIALLY_EFFECTIVE.value,
    ):
        if disp_str is None:
            return True
        raise ValueError(
            f"verdict {verdict_str} pairs only with null disposition; "
            f"got {disp_str!r}"
        )
    raise ValueError(f"unknown historical verdict {verdict_str!r}")


def _coerce_verdict(value: HistoricalVerdict | str) -> str:
    if isinstance(value, HistoricalVerdict):
        return value.value
    if isinstance(value, str):
        try:
            return HistoricalVerdict(value).value
        except ValueError as error:
            raise ValueError(
                f"unknown historical verdict {value!r}; expected one of "
                f"{[member.value for member in HistoricalVerdict]}"
            ) from error
    raise TypeError(
        f"historical_verdict must be a HistoricalVerdict | str, "
        f"got {type(value).__name__}"
    )


# --- finite-value post-check --------------------------------------


def _walk_finite(obj: Any, path: str) -> list[str]:
    """Walk ``obj`` and return a list of paths (dot/bracket-notation) whose
    values are non-finite floats (NaN, +Inf, -Inf). Integers and strings are
    skipped (they cannot be NaN/Inf).

    This is the robust way to reject non-finite values: ``jsonschema`` accepts
    ``Infinity`` / ``NaN`` as ``type: number`` instances (the JSON schema
    spec defers to ``json.loads``), so we run this python pass AFTER
    ``jsonschema`` reports clean.
    """
    bad: list[str] = []
    if isinstance(obj, bool):
        # bool is a subclass of int; ignore here.
        return bad
    if isinstance(obj, float):
        if not math.isfinite(obj):
            bad.append(path)
        return bad
    if isinstance(obj, int):
        return bad
    if isinstance(obj, dict):
        for key, value in obj.items():
            sub = f"{path}.{key}" if path else key
            bad.extend(_walk_finite(value, sub))
        return bad
    if isinstance(obj, list):
        for index, value in enumerate(obj):
            sub = f"{path}[{index}]"
            bad.extend(_walk_finite(value, sub))
        return bad
    # strings / None / anything else: skip.
    return bad


def _reject_non_finite(obj: Any, label: str) -> None:
    bad = _walk_finite(obj, "")
    if bad:
        paths = ", ".join(bad)
        raise ValueError(
            f"{label} contains non-finite (NaN/Infinity) value(s) at: {paths}; "
            "design §13 requires every numeric threshold to be finite"
        )


# --- preregistration schema ---------------------------------------


def validate_preregistration(draft: dict[str, Any]) -> dict[str, Any]:
    """Validate a preregistration draft against the §13 schema.

    Raises
    ------
    ValueError:
        If the draft fails JSON schema validation OR contains any non-finite
        numeric value. The error message from the finite check names the
        offending path; the JSON schema error cites the schema path.
    """
    if not isinstance(draft, dict):
        raise ValueError(
            f"preregistration draft must be a JSON object, got {type(draft).__name__}"
        )
    errors = sorted(
        _PREREGISTRATION_VALIDATOR.iter_errors(draft),
        key=lambda e: list(e.path),
    )
    if errors:
        messages = [_format_schema_error(error) for error in errors]
        raise ValueError(
            "preregistration draft failed schema validation:\n  - "
            + "\n  - ".join(messages)
        )
    _reject_non_finite(draft, "preregistration draft")
    return draft


# --- candidate event schema ---------------------------------------


def validate_candidate_event(record: dict[str, Any]) -> dict[str, Any]:
    """Validate one candidate event record.

    Raises ``ValueError`` on schema failure or non-finite numeric value
    (e.g. in ``parameters`` or ``metrics``).
    """
    if not isinstance(record, dict):
        raise ValueError(
            f"candidate event must be a JSON object, got {type(record).__name__}"
        )
    errors = sorted(
        _CANDIDATE_EVENT_VALIDATOR.iter_errors(record),
        key=lambda e: list(e.path),
    )
    if errors:
        messages = [_format_schema_error(error) for error in errors]
        raise ValueError(
            "candidate event failed schema validation:\n  - "
            + "\n  - ".join(messages)
        )
    _reject_non_finite(record, "candidate event")
    return record


def _format_schema_error(error: ValidationError) -> str:
    """Render a jsonschema ValidationError into an actionable message."""
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    return f"at {location}: {error.message}"
