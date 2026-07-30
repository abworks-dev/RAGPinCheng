"""Smoke tests for src.eval.metrics comparison grading + aggregation.

Covers Phase A plan sec 12 test matrix.
"""
from __future__ import annotations
import pytest
from src.eval.metrics import (
    ComparisonInputError, grade_comparison, grade_comparison_input,
    aggregate_comparison,
)


def _g(both, k=5):
    sides = 2
    ranks = [1, 2] if both else [1, None]
    return {"k": k, "sides_total": sides, "sides_hit": 2 if both else 1,
            "both_hit": both, "all_sides_hit": both, "any_side_hit": True,
            "side_ranks": ranks, "side_recall": 1.0 if both else 0.5,
            "side_mrr": 0.75 if both else 0.5}


def _item(item_id, g_off, g_on, status="applied", e5_off=None, e5_on=None):
    return {"item_id": item_id, "off": g_off, "on": g_on,
            "decompose_status": status,
            "errors": {"off_k5": e5_off, "off_k8": None,
                       "on_k5": e5_on, "on_k8": None}}


def test_input_rejects_empty_sides():
    with pytest.raises(ComparisonInputError):
        grade_comparison_input([], ["p1"], 5)


def test_input_rejects_single_side():
    with pytest.raises(ComparisonInputError):
        grade_comparison_input([["p1"]], ["p1"], 5)


def test_input_rejects_empty_inner_side():
    with pytest.raises(ComparisonInputError):
        grade_comparison_input([["p1"], []], ["p1"], 5)


def test_input_rejects_cross_overlap():
    with pytest.raises(ComparisonInputError):
        grade_comparison_input([["p1","p2"], ["p2","p3"]], ["p1","p2","p3"], 5)


def test_input_rejects_nonpositive_k():
    with pytest.raises(ComparisonInputError):
        grade_comparison_input([["p1"], ["p2"]], [], 0)
    with pytest.raises(ComparisonInputError):
        grade_comparison_input([["p1"], ["p2"]], [], -1)


def test_grade_keeps_legacy_shape():
    g = grade_comparison([["a1","a2"], ["b1","b2"]], ["a1","b1","x"], k=5)
    assert g["sides_total"] == 2 and g["sides_hit"] == 2
    assert g["both_hit"] is True and g["all_sides_hit"] is True
    assert g["any_side_hit"] is True and g["k"] == 5
    assert g["side_ranks"] == [1, 2]
    assert g["side_recall"] == 1.0 and g["side_mrr"] == pytest.approx(0.75)


def test_both_at_rank_1_and_k():
    g = grade_comparison([["a"], ["b"]], ["a", "b"], k=5)
    assert g["side_ranks"] == [1, 2] and g["both_hit"] is True


def test_second_side_outside_k():
    g = grade_comparison([["a"], ["b"]], ["a", "x", "y", "z", "b"], k=4)
    assert g["side_ranks"] == [1, None] and g["both_hit"] is False
    assert g["any_side_hit"] is True and g["side_recall"] == 0.5


def test_all_miss():
    g = grade_comparison([["a"], ["b"]], ["x", "y", "z"], k=5)
    assert g["side_ranks"] == [None, None]
    assert g["both_hit"] is False and g["any_side_hit"] is False
    assert g["side_recall"] == 0.0 and g["side_mrr"] == 0.0


def test_side_with_multiple_candidates_uses_first_hit():
    g = grade_comparison([["a1","a2","a3"], ["b"]], ["a2", "b"], k=5)
    assert g["side_ranks"] == [1, 2]


def test_three_sides_consistently():
    g = grade_comparison([["a"], ["b"], ["c"]], ["a", "c", "b"], k=5)
    assert g["sides_total"] == 3 and g["side_ranks"] == [1, 3, 2]
    assert g["side_recall"] == 1.0


def test_not_applied_zero_delta_in_itt():
    items = [_item("x", _g(True), _g(True), status="not_applied"),
             _item("y", _g(True), _g(True), status="not_applied")]
    a = aggregate_comparison(items, k=5, analysis_set="itt_complete_pairs")
    assert a["n_items_selected"] == 2 and a["n_decompose_not_applied"] == 2
    assert a["delta_all_sides_hit_rate"] == 0.0
    assert a["gain_count"] == 0 and a["loss_count"] == 0
    assert a["same_hit_count"] == 2 and a["same_miss_count"] == 0


def test_applied_only_excludes_not_applied():
    items = [_item("a", _g(False), _g(True), status="applied"),
             _item("b", _g(True), _g(True), status="not_applied")]
    a = aggregate_comparison(items, k=5, analysis_set="applied_only_complete_pairs")
    assert a["n_items_selected"] == 2
    assert a["n_decompose_applied"] == 1
    assert a["n_paired_evaluable"] == 1
    assert a["delta_all_sides_hit_rate"] == 1.0
    assert a["gain_count"] == 1


def test_applied_only_empty_returns_nulls():
    items = [_item("a", _g(True), _g(True), status="not_applied")]
    a = aggregate_comparison(items, k=5, analysis_set="applied_only_complete_pairs")
    assert a["n_items_selected"] == 1
    assert a["n_paired_evaluable"] == 0
    assert a["delta_all_sides_hit_rate"] is None
    assert a["delta_macro_side_mrr"] is None


def test_transitions_conserve():
    items = [_item("a", _g(False), _g(True), status="applied"),
             _item("b", _g(True), _g(False), status="applied"),
             _item("c", _g(True), _g(True), status="applied"),
             _item("d", _g(False), _g(False), status="applied")]
    a = aggregate_comparison(items, k=5, analysis_set="itt_complete_pairs")
    n = a["n_paired_evaluable"]
    assert n == 4
    assert a["gain_count"] + a["loss_count"] + a["same_hit_count"] + a["same_miss_count"] == n


def test_delta_zero_preserves_transitions():
    items = [_item("a", _g(False), _g(True), status="applied"),
             _item("b", _g(True), _g(False), status="applied")]
    a = aggregate_comparison(items, k=5, analysis_set="itt_complete_pairs")
    assert a["delta_all_sides_hit_rate"] == 0.0
    assert a["gain_count"] == 1 and a["loss_count"] == 1
    assert a["gain_count"] + a["loss_count"] + a["same_hit_count"] + a["same_miss_count"] == 2


def test_side_mrr_treats_miss_as_zero():
    items = [_item("a", _g(False), _g(True), status="applied")]
    a = aggregate_comparison(items, k=5, analysis_set="itt_complete_pairs")
    assert a["delta_macro_side_mrr"] == pytest.approx(0.25)


def test_excludes_error_items_from_quality():
    items = [_item("a", _g(True), _g(True)),
             _item("b", None, None, status="error", e5_off="timeout", e5_on="timeout")]
    a = aggregate_comparison(items, k=5, analysis_set="itt_complete_pairs")
    assert a["n_items_selected"] == 2 and a["n_paired_evaluable"] == 1
    assert a["n_error_off_k5"] == 1 and a["n_error_on_k5"] == 1
    assert "b" in a["error_item_ids"]


def test_mean_rank_given_hit_does_not_drive_payoff():
    items = [_item("a", _g(True), _g(True), status="applied")]
    a = aggregate_comparison(items, k=5, analysis_set="itt_complete_pairs")
    assert a["delta_macro_side_mrr"] == 0.0
    assert a["delta_mean_rank_given_hit"] == 0.0


def test_sample_size_warning_under_4():
    items = [_item(f"c{i}", _g(True), _g(True)) for i in range(4)]
    a = aggregate_comparison(items, k=5, analysis_set="itt_complete_pairs")
    assert a["n_paired_evaluable"] == 4
    assert any("sample_size 4" in w for w in a["run_warnings"])


def test_legacy_grade_one_unchanged():
    from src.eval.metrics import grade_one
    assert grade_one(["a","b"], ["c","a","b"]) == 2
    assert grade_one(["a"], ["b","c"]) is None
    assert grade_one([], []) is None
