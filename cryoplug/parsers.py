"""Parsers extracting model-quality metrics from program logs (Phenix, MolProbity, Servalcat...)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class MetricDef:
    key: str
    label: str
    patterns: list[str]
    unit: str = ""
    good: Callable[[float], bool] | None = None
    warn: Callable[[float], bool] | None = None
    target: str = ""

    def status(self, value: float) -> str | None:
        if self.good is None:
            return None
        if self.good(value):
            return "good"
        if self.warn is not None and self.warn(value):
            return "warn"
        return "bad"


METRICS: list[MetricDef] = [
    MetricDef("molprobity_score", "MolProbity score", [r"molprobity score\s*[:=]\s*([\d.]+)"],
              good=lambda v: v <= 2.0, warn=lambda v: v <= 3.0, target="≤ 2.0"),
    MetricDef("clashscore", "Clashscore", [r"clashscore\s*[:=]\s*([\d.]+)"],
              good=lambda v: v <= 10, warn=lambda v: v <= 20, target="≤ 10"),
    MetricDef("rama_outliers", "Ramachandran outliers", [r"ramachandran outliers\s*[:=]\s*([\d.]+)\s*%",
                                                          r"ramachandran plot:\s*\n\s*outliers\s*:\s*([\d.]+)\s*%"],
              unit="%", good=lambda v: v <= 0.5, warn=lambda v: v <= 2.0, target="≤ 0.5 %"),
    MetricDef("rama_favored", "Ramachandran favoured", [r"\bfavou?red\s*[:=]\s*([\d.]+)\s*%"],
              unit="%", good=lambda v: v >= 95, warn=lambda v: v >= 90, target="≥ 95 %"),
    MetricDef("rotamer_outliers", "Rotamer outliers", [r"rotamer outliers\s*[:=]\s*([\d.]+)\s*%",
                                                       r"rotamer:\s*\n\s*outliers\s*:\s*([\d.]+)\s*%"],
              unit="%", good=lambda v: v <= 1.0, warn=lambda v: v <= 3.0, target="≤ 1 %"),
    MetricDef("cbeta_deviations", "Cβ deviations", [r"c-?beta deviations\s*[:=]\s*([\d.]+)"],
              good=lambda v: v <= 1, warn=lambda v: v <= 5, target="0"),
    MetricDef("rms_bonds", "RMSD bonds", [r"rms\(bonds\)\s*[:=]\s*([\d.]+)", r"\bbonds?\s*:\s*([\d.]+)\s"],
              unit="Å", good=lambda v: v <= 0.01, warn=lambda v: v <= 0.02, target="≤ 0.01 Å"),
    MetricDef("rms_angles", "RMSD angles", [r"rms\(angles\)\s*[:=]\s*([\d.]+)", r"\bangles?\s*:\s*([\d.]+)\s"],
              unit="°", good=lambda v: v <= 1.5, warn=lambda v: v <= 2.0, target="≤ 1.5°"),
    MetricDef("cc_mask", "CC (mask)", [r"cc_mask\s*[:=]\s*(-?[\d.]+)"],
              good=lambda v: v >= 0.7, warn=lambda v: v >= 0.5, target="≥ 0.7"),
    MetricDef("cc_box", "CC (box)", [r"cc_box\s*[:=]\s*(-?[\d.]+)"]),
    MetricDef("cc_peaks", "CC (peaks)", [r"cc_peaks\s*[:=]\s*(-?[\d.]+)"]),
    MetricDef("cc_volume", "CC (volume)", [r"cc_volume\s*[:=]\s*(-?[\d.]+)"]),
    MetricDef("emringer", "EMRinger score", [r"emringer score\s*[:=]?\s*(-?[\d.]+)"],
              good=lambda v: v >= 2.0, warn=lambda v: v >= 1.0, target="≥ 2 (better than 4 Å)"),
]


def parse_metrics(text: str) -> dict[str, float]:
    """Last value of each known metric found in ``text``."""
    found: dict[str, float] = {}
    low = text.lower()
    for m in METRICS:
        for pattern in m.patterns:
            matches = re.findall(pattern, low)
            if matches:
                try:
                    found[m.key] = float(matches[-1])
                except ValueError:
                    continue
                break
    return found


def metrics_table(values: dict[str, float]) -> list[dict[str, Any]]:
    rows = []
    for m in METRICS:
        if m.key in values:
            v = values[m.key]
            rows.append({"label": m.label, "value": f"{v:g}{(' ' + m.unit) if m.unit else ''}",
                         "status": m.status(v), "target": m.target, "key": m.key, "raw": v})
    return rows


HIGHLIGHT_KEYS = ("molprobity_score", "cc_mask", "clashscore", "emringer")


def highlights(ctx, values: dict[str, float], limit: int = 3) -> None:
    by_key = {m.key: m for m in METRICS}
    n = 0
    for key in HIGHLIGHT_KEYS:
        if key in values and n < limit:
            m = by_key[key]
            ctx.add_highlight(m.label, f"{values[key]:g}", m.status(values[key]))
            n += 1
