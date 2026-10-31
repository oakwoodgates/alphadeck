"""Config as DATA — a run's dials come from a JSON overlay, never from a code edit.

Before this, sweeping any of the 83 ``CallConfig`` dials outside the six lab master switches meant editing
``domain/config.py`` (or hand-writing a variant list in ``replay/compare.py``). That makes an experiment
unreviewable and unreproducible: the dials that produced a result live in a diff nobody kept.

An overlay is a flat JSON object of ``{dial: value}``. It is applied to ``DEFAULT_CONFIG`` and the result is
what the whole run — detectors, PIT read bounds, assembler — is threaded with, and what the manifest
fingerprints.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from domain.config import DEFAULT_CONFIG, CallConfig, _canonical_config, config_hash


class OverlayError(ValueError):
    """An overlay that cannot be applied — an unknown dial or a value the model rejects."""


def apply_overlay(raw: dict[str, Any], *, base: CallConfig = DEFAULT_CONFIG) -> CallConfig:
    """``base`` with ``raw``'s dials applied, VALIDATED.

    Two guards:

    **Unknown dials fail loudly.** ``model_copy(update=...)`` happily sets an attribute the model never
    declared, so a typo (``insider_core_min_distnct``) would ride into a run, change nothing, and produce a
    result the operator would read as "that dial is inert on this tape". The message names the key. This is
    the guard that earns its keep — it catches the failure that is otherwise INVISIBLE.

    **The result is CONSTRUCTED, not copied.** Merging into ``base.model_dump()`` and going back through
    ``model_validate`` means the overlay's values are coerced and checked exactly as if the config had been
    written by hand — a JSON list becomes the declared ``frozenset[Kind]``, a string becomes the enum, an
    out-of-range value raises. ``model_copy(update=...)`` does NONE of that: it is a copy, so the raw JSON
    value is installed verbatim and the type declared on the field is a suggestion.

    **Be honest about the second guard's reach.** MEASURED at the time of writing: exactly ONE of
    ``CallConfig``'s 83 fields carries a value constraint (``insider_10b5_1_buy_weight``, ``ge=0, le=1``).
    So re-validation today buys type coercion and that one bound — it is not a claim that every nonsensical
    dial value is caught, because the model does not declare enough to catch them. A dial worth bounding
    should get its bound on ``CallConfig``, where the live path benefits too, rather than a check here.
    """
    unknown = sorted(set(raw) - set(CallConfig.model_fields))
    if unknown:
        raise OverlayError(
            f"unknown dial(s) in overlay: {unknown}. "
            f"An overlay may only set fields declared on CallConfig."
        )
    merged = base.model_dump()
    merged.update(raw)
    try:
        return CallConfig.model_validate(merged)
    except (
        Exception
    ) as exc:  # pydantic.ValidationError, kept broad so the message is the useful part
        raise OverlayError(f"overlay produced an invalid CallConfig: {exc}") from exc


def load_overlay(path: str | Path, *, base: CallConfig = DEFAULT_CONFIG) -> CallConfig:
    """Read an overlay JSON file and apply it. The file must hold a flat JSON object."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OverlayError(f"overlay {p} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise OverlayError(
            f"overlay {p} must be a JSON object of {{dial: value}}, got {type(raw).__name__}"
        )
    return apply_overlay(raw, base=base)


def load_overlay_ladder(path: str | Path) -> list[dict[str, Any]]:
    """Read an OVERLAY LADDER — a JSON array of flat overlays, one per point of ONE curve — and validate
    every point before any work (``backtest.sweep --overlay-ladder``).

    For a family whose dials move TOGETHER, which ``--ladder dial=v1,v2`` cannot express: F4's unit and
    session count are refused one without the other (``CallConfig._bar_window_units``), so each point has to
    set both, and the baseline is the point that sets the unit back to ``"calendar"``. Each item is exactly
    the ``backtest.run --config`` format. Refused, naming the file and the point: an empty ladder, a point
    that is not a JSON object, an unknown dial or invalid value (``apply_overlay``'s two guards), and two
    points that resolve to the same config — one measurement drawn twice on a curve reads as two.
    """
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OverlayError(f"overlay ladder {p} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise OverlayError(
            f"overlay ladder {p} must be a non-empty JSON array of {{dial: value}} objects"
        )
    seen: dict[str, int] = {}
    for i, point in enumerate(raw):
        if not isinstance(point, dict) or not point:
            raise OverlayError(
                f"overlay ladder {p} point {i} must be a non-empty JSON object of {{dial: value}}"
            )
        try:
            h = config_hash(apply_overlay(point))
        except OverlayError as exc:
            raise OverlayError(f"overlay ladder {p} point {i}: {exc}") from exc
        if h in seen:
            raise OverlayError(
                f"overlay ladder {p} points {seen[h]} and {i} resolve to the same config; a curve that "
                "draws one measurement twice reads as two"
            )
        seen[h] = i
    return raw


def overlay_diff(
    cfg: CallConfig, *, base: CallConfig = DEFAULT_CONFIG
) -> dict[str, dict[str, Any]]:
    """Every dial on which ``cfg`` differs from ``base``, as ``{dial: {"default": x, "run": y}}``.

    Compared on the CANONICAL form, not the raw attributes, for the same reason the fingerprint is: a
    ``frozenset`` dial's iteration order is process-dependent, so comparing raw values would report a
    spurious difference (or miss a real one) depending on ``PYTHONHASHSEED``. Rendering both sides through
    ``_canonical_config`` also makes the diff JSON-serializable by construction — it is written straight
    into the manifest.

    This is the human-readable half of run identity: ``config_hash`` says WHETHER two runs used the same
    dials, this says WHICH ones moved.
    """
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(CallConfig.model_fields):
        run_v = _canonical_config(getattr(cfg, name))
        base_v = _canonical_config(getattr(base, name))
        if run_v != base_v:
            out[name] = {"default": base_v, "run": run_v}
    return out
