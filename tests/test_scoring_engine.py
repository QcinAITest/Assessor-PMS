"""
Unit tests for app/services/scoring_engine.py.

No DB or HTTP — pure function testing.
"""
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers to build lightweight mock objects
# ---------------------------------------------------------------------------
def make_param(code, data_type, weight=0, parent_id=None, children=None):
    p = MagicMock()
    p.code = code
    p.data_type = data_type
    p.weight = weight
    p.parent_id = parent_id
    p.children = children or []
    p.is_top_level = parent_id is None
    p.options = []
    return p


def make_form(parameters, essential_criteria=None):
    ft = MagicMock()
    ft.parameters = parameters
    ft.essential_criteria = essential_criteria or []
    return ft


def make_board(engine="numeric", bands=None, window=10):
    b = MagicMock()
    b.rating_engine = engine
    b.config = {"cumulative_window": window}
    if bands is None:
        bands = [
            {"min": 4.5, "max": 5.0, "stars": 5},
            {"min": 4.0, "max": 4.49, "stars": 4},
            {"min": 3.5, "max": 3.99, "stars": 3},
            {"min": 3.0, "max": 3.49, "stars": 2},
            {"min": 0.0, "max": 2.99, "stars": 1},
        ]
    b.star_bands = bands
    return b


# ---------------------------------------------------------------------------
# normalize_weights
# ---------------------------------------------------------------------------
class TestNormalizeWeights:
    from app.services.scoring_engine import normalize_weights

    def test_equal_weights_three_params(self):
        from app.services.scoring_engine import normalize_weights
        params = [
            make_param("C1", "CALCULATED", weight=100, parent_id=None),
            make_param("C2", "CALCULATED", weight=100, parent_id=None),
            make_param("C3", "CALCULATED", weight=100, parent_id=None),
        ]
        weights = normalize_weights(params)
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        assert abs(weights["C1"] - 1/3) < 1e-9

    def test_unequal_weights_normalise_to_1(self):
        from app.services.scoring_engine import normalize_weights
        params = [
            make_param("A", "CALCULATED", weight=60, parent_id=None),
            make_param("B", "CALCULATED", weight=40, parent_id=None),
        ]
        w = normalize_weights(params)
        assert abs(sum(w.values()) - 1.0) < 1e-9
        assert abs(w["A"] - 0.6) < 1e-9

    def test_zero_weights_distribute_evenly(self):
        from app.services.scoring_engine import normalize_weights
        params = [
            make_param("X", "CALCULATED", weight=0, parent_id=None),
            make_param("Y", "CALCULATED", weight=0, parent_id=None),
        ]
        w = normalize_weights(params)
        assert abs(w["X"] - 0.5) < 1e-9

    def test_child_params_excluded(self):
        from app.services.scoring_engine import normalize_weights
        top = make_param("P1", "CALCULATED", weight=100, parent_id=None)
        child = make_param("P1_S1", "RATING_1_5", weight=0, parent_id="P1")
        w = normalize_weights([top, child])
        assert "P1_S1" not in w
        assert abs(w["P1"] - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# calculate_parameter_score
# ---------------------------------------------------------------------------
class TestCalculateParameterScore:
    def test_rating_1_5(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("C1_S1", "RATING_1_5")
        assert calculate_parameter_score(p, {"C1_S1": 4}) == 4.0

    def test_yes_no_yes(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("ESS", "YES_NO")
        assert calculate_parameter_score(p, {"ESS": "YES"}) == 5.0

    def test_yes_no_no(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("ESS", "YES_NO")
        assert calculate_parameter_score(p, {"ESS": "NO"}) == 1.0

    def test_yes_no_case_insensitive(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("ESS", "YES_NO")
        assert calculate_parameter_score(p, {"ESS": "yes"}) == 5.0

    def test_percentage_100(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("P", "PERCENTAGE")
        assert abs(calculate_parameter_score(p, {"P": 100}) - 5.0) < 1e-9

    def test_percentage_0(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("P", "PERCENTAGE")
        assert calculate_parameter_score(p, {"P": 0}) == 1.0

    def test_percentage_50(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("P", "PERCENTAGE")
        # 50/20 = 2.5
        assert abs(calculate_parameter_score(p, {"P": 50}) - 2.5) < 1e-9

    def test_calculated_averages_children(self):
        from app.services.scoring_engine import calculate_parameter_score
        c1 = make_param("C1_S1", "RATING_1_5")
        c2 = make_param("C1_S2", "RATING_1_5")
        top = make_param("C1", "CALCULATED", children=[c1, c2])
        score = calculate_parameter_score(top, {"C1_S1": 3, "C1_S2": 5})
        assert abs(score - 4.0) < 1e-9

    def test_missing_response_returns_none(self):
        from app.services.scoring_engine import calculate_parameter_score
        p = make_param("C1", "RATING_1_5")
        assert calculate_parameter_score(p, {}) is None

    def test_calculated_no_children_returns_none(self):
        from app.services.scoring_engine import calculate_parameter_score
        top = make_param("C1", "CALCULATED", children=[])
        assert calculate_parameter_score(top, {}) is None


# ---------------------------------------------------------------------------
# calculate_form_score
# ---------------------------------------------------------------------------
class TestCalculateFormScore:
    def _build_form_with_responses(self, responses, essential_responses=None):
        """Build a 2-top-level-param form (C1=60%, C2=40%) + optional essential criteria."""
        c1_s1 = make_param("C1_S1", "RATING_1_5", parent_id="C1")
        c1 = make_param("C1", "CALCULATED", weight=60, parent_id=None, children=[c1_s1])
        c2_s1 = make_param("C2_S1", "RATING_1_5", parent_id="C2")
        c2 = make_param("C2", "CALCULATED", weight=40, parent_id=None, children=[c2_s1])

        essentials = []
        for code, val in (essential_responses or {}).items():
            ec = MagicMock()
            ec.code = code
            essentials.append(ec)

        form = make_form([c1, c2, c1_s1, c2_s1], essential_criteria=essentials)
        return form

    def test_weighted_average(self):
        from app.services.scoring_engine import calculate_form_score
        c1_s1 = make_param("C1_S1", "RATING_1_5", parent_id="C1")
        c1 = make_param("C1", "CALCULATED", weight=60, parent_id=None, children=[c1_s1])
        c2_s1 = make_param("C2_S1", "RATING_1_5", parent_id="C2")
        c2 = make_param("C2", "CALCULATED", weight=40, parent_id=None, children=[c2_s1])
        form = make_form([c1, c2, c1_s1, c2_s1])
        # C1 = 4, C2 = 2 → weighted = 4*0.6 + 2*0.4 = 3.2
        score, flag = calculate_form_score(form, {"C1_S1": 4, "C2_S1": 2})
        assert abs(score - 3.2) < 0.01
        assert flag is False

    def test_essential_flag_on_no(self):
        from app.services.scoring_engine import calculate_form_score
        c1_s1 = make_param("C1_S1", "RATING_1_5", parent_id="C1")
        c1 = make_param("C1", "CALCULATED", weight=100, parent_id=None, children=[c1_s1])
        ec = MagicMock()
        ec.code = "ESS_ETHICS"
        form = make_form([c1, c1_s1], essential_criteria=[ec])
        _, flag = calculate_form_score(form, {"C1_S1": 5, "ESS_ETHICS": "NO"})
        assert flag is True

    def test_no_essential_flag_when_all_yes(self):
        from app.services.scoring_engine import calculate_form_score
        c1_s1 = make_param("C1_S1", "RATING_1_5", parent_id="C1")
        c1 = make_param("C1", "CALCULATED", weight=100, parent_id=None, children=[c1_s1])
        ec = MagicMock()
        ec.code = "ESS_ETHICS"
        form = make_form([c1, c1_s1], essential_criteria=[ec])
        _, flag = calculate_form_score(form, {"C1_S1": 5, "ESS_ETHICS": "YES"})
        assert flag is False

    def test_empty_responses_score_zero(self):
        from app.services.scoring_engine import calculate_form_score
        c1 = make_param("C1", "CALCULATED", weight=100, parent_id=None, children=[])
        form = make_form([c1])
        score, _ = calculate_form_score(form, {})
        assert score == 0.0


# ---------------------------------------------------------------------------
# Star rating functions
# ---------------------------------------------------------------------------
class TestStarRating:
    def test_numeric_perfect_five(self):
        from app.services.scoring_engine import score_to_star_numeric
        bands = [
            {"min": 4.5, "stars": 5}, {"min": 4.0, "stars": 4},
            {"min": 3.5, "stars": 3}, {"min": 3.0, "stars": 2}, {"min": 0, "stars": 1},
        ]
        assert score_to_star_numeric(5.0, bands) == 5
        assert score_to_star_numeric(4.5, bands) == 5
        assert score_to_star_numeric(4.49, bands) == 4
        assert score_to_star_numeric(1.0, bands) == 1

    def test_percentage_bands(self):
        from app.services.scoring_engine import score_to_star_percentage
        bands = [
            {"min_pct": 80, "stars": 5}, {"min_pct": 65, "stars": 4},
            {"min_pct": 50, "stars": 3}, {"min_pct": 35, "stars": 2}, {"min_pct": 0, "stars": 1},
        ]
        assert score_to_star_percentage(85, bands) == 5
        assert score_to_star_percentage(70, bands) == 4
        assert score_to_star_percentage(55, bands) == 3
        assert score_to_star_percentage(10, bands) == 1

    def test_get_star_rating_dispatches_by_engine(self):
        from app.services.scoring_engine import get_star_rating
        num_board = make_board("numeric")
        pct_board = make_board("percentage", bands=[
            {"min_pct": 80, "stars": 5}, {"min_pct": 0, "stars": 1},
        ])
        assert get_star_rating(num_board, 5.0) == 5
        assert get_star_rating(pct_board, 90) == 5
        assert get_star_rating(pct_board, 50) == 1

    def test_numeric_boundary_conditions(self):
        from app.services.scoring_engine import score_to_star_numeric
        bands = [
            {"min": 4.5, "stars": 5}, {"min": 4.0, "stars": 4},
            {"min": 3.0, "stars": 3}, {"min": 0, "stars": 1},
        ]
        assert score_to_star_numeric(4.0, bands) == 4
        assert score_to_star_numeric(3.999, bands) == 3
        assert score_to_star_numeric(0.0, bands) == 1


# ---------------------------------------------------------------------------
# normalize_to_base100
# ---------------------------------------------------------------------------
class TestNormalizeToBase100:
    def test_numeric_min_maps_to_0(self):
        from app.services.scoring_engine import normalize_to_base100
        assert normalize_to_base100(1.0, "numeric") == 0.0

    def test_numeric_max_maps_to_100(self):
        from app.services.scoring_engine import normalize_to_base100
        assert normalize_to_base100(5.0, "numeric") == 100.0

    def test_numeric_midpoint(self):
        from app.services.scoring_engine import normalize_to_base100
        assert normalize_to_base100(3.0, "numeric") == 50.0

    def test_percentage_passthrough(self):
        from app.services.scoring_engine import normalize_to_base100
        assert normalize_to_base100(75.0, "percentage") == 75.0

    def test_clamp_above_100(self):
        from app.services.scoring_engine import normalize_to_base100
        assert normalize_to_base100(200, "percentage") == 100.0

    def test_clamp_below_0(self):
        from app.services.scoring_engine import normalize_to_base100
        assert normalize_to_base100(-10, "percentage") == 0.0
