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
    lo: float = 0.0  # plausible range: a value outside it is a misread of the log (another line caught), not a result
    hi: float = float("inf")

    def plausible(self, value: float) -> bool:
        return self.lo <= value <= self.hi

    def status(self, value: float) -> str | None:
        if self.good is None:
            return None
        if self.good(value):
            return "good"
        if self.warn is not None and self.warn(value):
            return "warn"
        return "bad"


# A percentage of the Ramachandran or rotamer block, read inside that block only: Phenix prints the two blocks the
# same way ("Ramachandran plot:" then outliers / allowed / favored lines, or all on one line), and the rotamer block
# comes last, so a bare "favored" pattern would take the rotamer value.
PCT = r"([\d.]+)\s*%"
_RAMA_BLOCK = r"ramachandran plot\s*(?:\(ramalyze\))?\s*:[ \t]*\n(?:[ \t]*(?:outliers|allowed)[ \t]*[:=][ \t]*[\d.]+[ \t]*%[ \t]*\n){0,2}[ \t]*"
_ROTA_BLOCK = r"rotamer\s*(?:\(rotalyze\))?\s*:[ \t]*\n(?:[ \t]*(?:allowed|favou?red)[ \t]*[:=][ \t]*[\d.]+[ \t]*%[ \t]*\n){0,2}[ \t]*"
_RAMA_LINE = r"ramachandran plot[^\n:]*:[^\n]*?"
_ROTA_LINE = r"rotamer[^\n:]*:[^\n]*?"

METRICS: list[MetricDef] = [
    MetricDef("molprobity_score", "MolProbity score", [r"molprobity score\s*[:=]\s*([\d.]+)"],
              good=lambda v: v <= 2.0, warn=lambda v: v <= 3.0, target="≤ 2.0", hi=10.0),
    MetricDef("clashscore", "Clashscore", [r"clashscore\s*[:=]\s*([\d.]+)"],
              good=lambda v: v <= 10, warn=lambda v: v <= 20, target="≤ 10", hi=10000.0),
    MetricDef("rama_outliers", "Ramachandran outliers", [r"ramachandran outliers\s*[:=]\s*" + PCT,
                                                          _RAMA_BLOCK + r"outliers\s*[:=]\s*" + PCT,
                                                          _RAMA_LINE + r"outliers\s*[:=]?\s*" + PCT],
              unit="%", good=lambda v: v <= 0.5, warn=lambda v: v <= 2.0, target="≤ 0.5 %", hi=100.0),
    MetricDef("rama_favored", "Ramachandran favoured", [r"ramachandran favou?red\s*[:=]\s*" + PCT,
                                                        # MolProbity's summary: "Ramachandran outliers = ..." then "favored = ..."
                                                        r"ramachandran outliers\s*[:=]\s*[\d.]+\s*%[ \t]*\n[ \t]*favou?red\s*[:=]\s*" + PCT,
                                                        _RAMA_BLOCK + r"favou?red\s*[:=]\s*" + PCT,
                                                        _RAMA_LINE + r"favou?red\s*[:=]?\s*" + PCT],
              unit="%", good=lambda v: v >= 95, warn=lambda v: v >= 90, target="≥ 95 %", hi=100.0),
    MetricDef("rotamer_outliers", "Rotamer outliers", [r"rotamer outliers\s*[:=]\s*" + PCT,
                                                       _ROTA_BLOCK + r"outliers\s*[:=]\s*" + PCT,
                                                       _ROTA_LINE + r"outliers\s*[:=]?\s*" + PCT],
              unit="%", good=lambda v: v <= 1.0, warn=lambda v: v <= 3.0, target="≤ 1 %", hi=100.0),
    MetricDef("cbeta_deviations", "Cβ deviations", [r"c-?beta deviations\s*[:=]\s*([\d.]+)"],
              good=lambda v: v <= 1, warn=lambda v: v <= 5, target="0", hi=100000.0),
    # "bonds :" preceded by a letter or a hyphen is another count ("Total declared H-bonds : 1545")
    MetricDef("rms_bonds", "RMSD bonds", [r"rms\(bonds\)\s*[:=]\s*([\d.]+)", r"(?<![\w-])bonds?\s*:\s*([\d.]+)\s",
                                          r"bond lengths:\s*([\d.]+)"],
              unit="Å", good=lambda v: v <= 0.01, warn=lambda v: v <= 0.02, target="≤ 0.01 Å", hi=0.5),
    MetricDef("rms_angles", "RMSD angles", [r"rms\(angles\)\s*[:=]\s*([\d.]+)", r"(?<![\w-])angles?\s*:\s*([\d.]+)\s",
                                            r"bond angles:\s*([\d.]+)"],
              unit="°", good=lambda v: v <= 1.5, warn=lambda v: v <= 2.0, target="≤ 1.5°", hi=30.0),
    MetricDef("cc_mask", "CC (mask)", [r"cc_mask\s*[:=]\s*(-?[\d.]+)"],
              good=lambda v: v >= 0.7, warn=lambda v: v >= 0.5, target="≥ 0.7", lo=-1.0, hi=1.0),
    MetricDef("cc_box", "CC (box)", [r"cc_box\s*[:=]\s*(-?[\d.]+)"], lo=-1.0, hi=1.0),
    MetricDef("fsc_average", "Map-model FSC average", [r"fscaverage\(full\)\s*=\s*(-?[\d.]+)"],
              good=lambda v: v >= 0.6, warn=lambda v: v >= 0.4, lo=-1.0, hi=1.0),
    MetricDef("cc_peaks", "CC (peaks)", [r"cc_peaks\s*[:=]\s*(-?[\d.]+)"], lo=-1.0, hi=1.0),
    MetricDef("cc_volume", "CC (volume)", [r"cc_volume\s*[:=]\s*(-?[\d.]+)"], lo=-1.0, hi=1.0),
    MetricDef("emringer", "EMRinger score", [r"emringer score\s*[:=]?\s*(-?[\d.]+)"],
              good=lambda v: v >= 2.0, warn=lambda v: v >= 1.0, target="≥ 2 (better than 4 Å)", lo=-10.0, hi=10.0),
]


def parse_metrics(text: str, problems: list[str] | None = None) -> dict[str, float]:
    """The last plausible value of each known metric found in ``text`` (the patterns of a metric are tried in order,
    the first one with a plausible value wins). A metric whose every reading is implausible is left out, and
    ``problems``, when given, says so: a number out of range is a misread of the log, not a result."""
    found: dict[str, float] = {}
    low = text.lower()
    for m in METRICS:
        rejected: list[str] = []
        for pattern in m.patterns:
            matches = re.findall(pattern, low)
            value = None
            for raw in reversed(matches):
                try:
                    candidate = float(raw)
                except ValueError:
                    continue
                if m.plausible(candidate):
                    value = candidate
                    break
                rejected.append(raw)
            if value is not None:
                found[m.key] = value
                break
        if m.key not in found and rejected and problems is not None:
            problems.append(f"{m.label}: the value read ({', '.join(dict.fromkeys(rejected))}) is not plausible for this "
                            "metric and was left out (another line of the log was caught)")
    return found


def read_metrics(ctx: Any, text: str) -> dict[str, float]:
    """``parse_metrics`` for a job: an implausible reading is a warning in its log, not a bad result."""
    problems: list[str] = []
    values = parse_metrics(text, problems)
    for problem in problems:
        ctx.warn(problem)
    return values


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
