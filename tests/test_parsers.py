"""The reader of the Phenix / MolProbity logs: each value taken inside its own block (the rotamer block repeats the
Ramachandran lines, a count of hydrogen bonds looks like "bonds :"), and an implausible reading dropped with a
warning rather than shown as a bad result. The snippets come from Phenix 2.2.1 logs reported by a user."""
from __future__ import annotations

from cryoplug.parsers import METRICS, metrics_table, parse_metrics

PHENIX2_ONE_LINE = """
                          DEVIATIONS FROM IDEAL VALUES
  BOND      :  0.004   0.037   4152
  ANGLE     :  0.721   8.108   5636
  CHIRALITY :  0.040   0.210    612

  RAMACHANDRAN PLOT:  OUTLIERS 0.45 %  ALLOWED 3.98 %  FAVORED 95.57 %
  ROTAMER:            OUTLIERS 1.12 %  ALLOWED 9.78 %  FAVORED 89.10 %
  CBETA DEVIATIONS : 0
  CLASHSCORE       : 4.56
  MolProbity score : 1.42

  Secondary structure restraints:
    Total declared H-bonds : 1545
  CC_mask : 0.8123
"""

PHENIX2_BLOCKS = """
  MolProbity statistics.
    All-atom clashscore : 4.56
    Ramachandran plot:
      outliers :  0.45 %
      allowed  :  3.98 %
      favored  : 95.57 %
    Rotamer:
      outliers :  1.12 %
      allowed  :  9.78 %
      favored  : 89.10 %
  MolProbity score : 1.42
  Number of bonds : 4152
  rms(bonds) : 0.0040
  rms(angles) : 0.72
"""

MOLPROBITY_SUMMARY = """
================================ Summary =================================

  Ramachandran outliers =   0.45 %
                favored =  95.57 %
  Rotamer outliers      =   1.12 %
  C-beta deviations     =     0
  Clashscore            =   4.56
  RMS(bonds)            =   0.0040
  RMS(angles)           =   0.72
  MolProbity score      =   1.42
"""


def test_each_value_is_read_inside_its_block():
    for text in (PHENIX2_ONE_LINE, PHENIX2_BLOCKS, MOLPROBITY_SUMMARY):
        v = parse_metrics(text)
        assert v["rama_favored"] == 95.57, text  # not the 89.10 of the rotamer block
        assert v["rama_outliers"] == 0.45 and v["rotamer_outliers"] == 1.12 and v["clashscore"] == 4.56
        assert v["molprobity_score"] == 1.42 and v["rms_bonds"] == 0.004, text  # not the 1545 declared H-bonds
    assert parse_metrics(PHENIX2_ONE_LINE)["cc_mask"] == 0.8123
    assert parse_metrics(PHENIX2_BLOCKS)["rms_angles"] == 0.72 and parse_metrics(MOLPROBITY_SUMMARY)["rms_angles"] == 0.72


def test_implausible_readings_are_dropped_with_a_warning():
    problems: list[str] = []
    v = parse_metrics("  Total declared H-bonds : 1545\n  All bonds : 4152\n  favored : 120 %\n", problems)
    assert "rms_bonds" not in v and "rama_favored" not in v
    assert any(p.startswith("RMSD bonds") and "4152" in p for p in problems), problems
    assert not any("1545" in p for p in problems)  # never a candidate: the pattern leaves H-bonds alone
    # a plausible reading earlier in the log wins over an implausible one after it
    v = parse_metrics("  BOND : 0.004\n  Number of bonds : 4152\n", problems)
    assert v["rms_bonds"] == 0.004
    rows = {r["key"]: r for r in metrics_table(parse_metrics(PHENIX2_ONE_LINE))}
    assert rows["rms_bonds"]["status"] == "good" and rows["rama_favored"]["status"] == "good"


def test_every_metric_has_a_plausible_range():
    for m in METRICS:
        assert m.lo < m.hi and m.plausible((m.lo + min(m.hi, 1.0)) / 2) or m.plausible(m.lo), m.key
    assert not next(m for m in METRICS if m.key == "rms_bonds").plausible(1545)
