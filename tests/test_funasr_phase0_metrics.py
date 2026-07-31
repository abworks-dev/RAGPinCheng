"""Phase 0 ASR sandbox — metrics unit tests.

Per R2 spec §六 / §十五: pure-Python tests, no pytest required,
no GPU / network / model dependencies.
"""
from __future__ import annotations

import os
import sys
import unittest

# Make the sandbox importable
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from scripts.funasr_phase0.lib_metrics import (  # noqa: E402
    cer, rtf, realtime_speedup, code_metrics, bim_term_metrics,
    segment_metrics, Segment, _strip_punct_ws, CER_NORM_VERSION,
    _monotone_one_to_one,
)


class TestCER(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(cer("hello world", "hello world"), 0.0)
    def test_faster_than_realtime_implies_lower_cer_for_clean(self):
        self.assertLess(cer("hello world", "hello world"), 0.01)
    def test_mismatch(self):
        self.assertGreater(cer("hello", "helo"), 0.0)
        self.assertLess(cer("hello", "helo"), 1.0)
    def test_empty_reference(self):
        self.assertEqual(cer("", ""), 0.0)
        self.assertEqual(cer("", "x"), 1.0)
    def test_empty_hypothesis(self):
        self.assertEqual(cer("hello", ""), 1.0)
    def test_chinese_unicode(self):
        self.assertEqual(cer("你好世界", "你好世界"), 0.0)
        self.assertGreater(cer("你好", "你坏"), 0.0)
    def test_punctuation_stripped(self):
        self.assertEqual(cer("hello, world!", "hello world"), 0.0)


class TestRTF(unittest.TestCase):
    def test_faster_than_realtime_yields_rtf_under_one(self):
        # 60s audio, 30s wallclock -> RTF = 0.5 (faster than realtime)
        self.assertEqual(rtf(60.0, 30.0), 0.5)
    def test_slower_than_realtime_yields_rtf_above_one(self):
        self.assertEqual(rtf(60.0, 120.0), 2.0)
    def test_realtime_speedup_inverse(self):
        self.assertEqual(realtime_speedup(60.0, 30.0), 2.0)
    def test_zero_audio_duration(self):
        self.assertEqual(rtf(0.0, 30.0), float("inf"))
    def test_zero_wallclock(self):
        self.assertEqual(realtime_speedup(60.0, 0.0), float("inf"))


class TestCodeMetrics(unittest.TestCase):
    def test_no_codes(self):
        m = code_metrics("hello world", "hello world")
        self.assertEqual(m.precision, 0.0)
        self.assertEqual(m.recall, 0.0)
    def test_year_optional(self):
        # ref has year, hyp doesn't -> still match (year optional in canonical)
        m = code_metrics("GB 50017-2017", "GB 50017")
        self.assertEqual(m.recall, 1.0)
    def test_hallucinated_year(self):
        # ref has no year, hyp has year -> match (year optional)
        m = code_metrics("GB 50017", "GB 50017-2017")
        self.assertEqual(m.recall, 1.0)
        self.assertFalse(m.per_item[0]["year_match"])
    def test_wrong_code_is_fp(self):
        m = code_metrics("GB 50017", "GB 99999")
        self.assertEqual(m.true_positive, 0)
        self.assertEqual(m.false_positive, 1)
        self.assertEqual(m.false_negative, 1)
    def test_normalize_family_and_jgjt(self):
        m = code_metrics("JGJ-T 99-2015 和 CJJ 1-2008",
                        "jgj/t 99-2015 cjj 1-2008")
        self.assertEqual(m.true_positive, 2)
        self.assertEqual(m.recall, 1.0)
    def test_case_insensitive(self):
        m = code_metrics("gb 50017", "GB 50017")
        self.assertEqual(m.recall, 1.0)


class TestBIMTermMetrics(unittest.TestCase):
    def test_perfect(self):
        m = bim_term_metrics("钢结构 螺栓 GB 50017", "钢结构 螺栓 GB 50017")
        self.assertEqual(m.recall, 1.0)
        self.assertEqual(m.precision, 1.0)
    def test_false_positive_typo_correction(self):
        # model "corrected" 钢构造 to 钢结构 (forced in by hotword) -> FP
        m = bim_term_metrics("", "钢结构")
        self.assertEqual(m.recall, 0.0)
        self.assertEqual(m.precision, 0.0)
        self.assertEqual(len([d for d in m.false_positive_detail if d["verdict"] == "FP"]), 1)
    def test_fn_missed_term(self):
        # ref has 钢结构 + 螺栓 + GB 50017; hyp has 螺栓 + GB 50017
        # DEFAULT_BIM_TERMS contains "钢结构" and "螺栓连接" (not bare 螺栓)
        # and "GB 50017". So:
        #   钢结构: in_ref, not in_hyp -> FN
        #   螺栓连接: not in_ref, not in_hyp -> TN
        #   GB 50017: in_ref, in_hyp -> TP
        # n_tp = 1, n_fn = 1, recall = 1/2 = 0.5
        m = bim_term_metrics("钢结构 螺栓 GB 50017", "螺栓 GB 50017")
        self.assertEqual(m.recall, 0.5)
    def test_precision_with_typo(self):
        # reference has correct term; hypothesis has wrong forced term
        # this should produce precision=1.0 (no FP) but recall < 1
        m = bim_term_metrics("GB 50017", "GB 50017")
        self.assertEqual(m.precision, 1.0)
        self.assertEqual(m.recall, 1.0)


class TestSegmentMetrics(unittest.TestCase):
    def test_alignment_indices_are_strictly_monotone(self):
        refs = [Segment(100, 180, "r1"), Segment(110, 190, "r2")]
        hyps = [Segment(0, 80, "h1"), Segment(105, 185, "h2")]
        pairs = _monotone_one_to_one(refs, hyps)
        hyp_indices = [p[1] for p in pairs]
        self.assertEqual(hyp_indices, sorted(hyp_indices))
        self.assertEqual(len(hyp_indices), len(set(hyp_indices)))
    def test_one_to_one_no_collapse(self):
        # Two refs overlapping, one hyp cannot match both
        m = segment_metrics(
            [Segment(0, 100, "r1"), Segment(50, 150, "r2")],
            [Segment(0, 100, "h1")],
        )
        # hyp can only match one of the two refs
        self.assertEqual(m.n_matched, 1)
        self.assertAlmostEqual(m.omission_rate, 0.5)

    def test_drift_p50_p95_p99(self):
        m = segment_metrics(
            [Segment(0, 1000, "a"), Segment(1000, 2000, "b"), Segment(2000, 3000, "c")],
            [Segment(50, 1000, "a"), Segment(1100, 2000, "b"), Segment(2100, 3000, "c")],
        )
        self.assertEqual(m.start_drift_p50_ms, 100.0)
        self.assertEqual(m.start_drift_p95_ms, 100.0)
        self.assertEqual(m.start_drift_p99_ms, 100.0)

    def test_omission_rate(self):
        m = segment_metrics(
            [Segment(0, 1000, "a"), Segment(1000, 2000, "b")],
            [Segment(0, 1000, "a")],
        )
        self.assertEqual(m.omission_rate, 0.5)
        self.assertEqual(m.extra_rate, 0.0)

    def test_extra_rate(self):
        m = segment_metrics(
            [Segment(0, 1000, "a")],
            [Segment(0, 1000, "a"), Segment(1000, 2000, "b")],
        )
        self.assertEqual(m.omission_rate, 0.0)
        self.assertEqual(m.extra_rate, 0.5)

    def test_consecutive_repeat(self):
        m = segment_metrics(
            [Segment(0, 1000, "a"), Segment(1000, 2000, "b")],
            [Segment(0, 500, "x"), Segment(500, 1000, "x"),
             Segment(1000, 2000, "y")],
        )
        self.assertGreater(m.consecutive_repeat_rate, 0.0)
        self.assertAlmostEqual(m.consecutive_repeat_rate, 2 / 3)

    def test_empty_inputs(self):
        m1 = segment_metrics([], [Segment(0, 1000, "x")])
        self.assertEqual(m1.omission_rate, 0.0)
        m2 = segment_metrics([Segment(0, 1000, "x")], [])
        self.assertEqual(m2.omission_rate, 1.0)


class TestCERNormVersion(unittest.TestCase):
    def test_version_is_set(self):
        self.assertTrue(CER_NORM_VERSION.startswith("cer-norm/"))


if __name__ == "__main__":
    unittest.main()
