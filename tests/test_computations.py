"""
Unit tests for computations module — health score and gauge color functions.
"""
import pytest

from app.computations import (
    check_firmware_consistency,
    compute_health_score,
    compute_scorecard_score,
    filter_nonzero_segments,
    get_bandwidth_gauge_color,
    get_gauge_color,
    get_health_gauge_color,
    get_signal_bar_data,
    score_to_grade,
)


# ── compute_health_score ───────────────────────────────────────────────────


class TestComputeHealthScore:
    def test_perfect_metrics_returns_100(self):
        score = compute_health_score(
            green_nodes=10, total_nodes=10,
            avg_signal_dbm=-30, uptime_24h=100, bandwidth_utilization=0,
        )
        assert score == 100

    def test_worst_metrics_returns_0(self):
        score = compute_health_score(
            green_nodes=0, total_nodes=10,
            avg_signal_dbm=-90, uptime_24h=0, bandwidth_utilization=100,
        )
        assert score == 0

    def test_total_nodes_zero_gives_node_score_zero(self):
        score = compute_health_score(
            green_nodes=0, total_nodes=0,
            avg_signal_dbm=-60, uptime_24h=50, bandwidth_utilization=50,
        )
        # nodeScore=0, signalScore=50, uptimeScore=50, bandwidthScore=50
        # 0*0.25 + 50*0.25 + 50*0.25 + 50*0.25 = 37.5 → 38
        assert score == 38

    def test_half_metrics(self):
        score = compute_health_score(
            green_nodes=5, total_nodes=10,
            avg_signal_dbm=-60, uptime_24h=50, bandwidth_utilization=50,
        )
        # nodeScore=50, signalScore=50, uptimeScore=50, bandwidthScore=50
        # all 50 → 50
        assert score == 50

    def test_result_is_integer(self):
        score = compute_health_score(
            green_nodes=3, total_nodes=7,
            avg_signal_dbm=-55, uptime_24h=72, bandwidth_utilization=33,
        )
        assert isinstance(score, int)

    def test_result_clamped_to_0_100(self):
        # Even with extreme signal values, result stays in range
        score = compute_health_score(
            green_nodes=10, total_nodes=10,
            avg_signal_dbm=0, uptime_24h=100, bandwidth_utilization=0,
        )
        assert 0 <= score <= 100

    def test_signal_below_minus_90_clamps_to_zero(self):
        score = compute_health_score(
            green_nodes=10, total_nodes=10,
            avg_signal_dbm=-100, uptime_24h=100, bandwidth_utilization=0,
        )
        # nodeScore=100, signalScore=0 (clamped), uptimeScore=100, bandwidthScore=100
        # 100*0.25 + 0*0.25 + 100*0.25 + 100*0.25 = 75
        assert score == 75

    def test_signal_above_minus_30_clamps_to_100(self):
        score = compute_health_score(
            green_nodes=10, total_nodes=10,
            avg_signal_dbm=-10, uptime_24h=100, bandwidth_utilization=0,
        )
        # signalScore clamped to 100
        assert score == 100


# ── get_gauge_color ────────────────────────────────────────────────────────


class TestGetGaugeColor:
    def test_value_above_highest_threshold(self):
        thresholds = [(80, '#4CAF50'), (50, '#FFC107')]
        assert get_gauge_color(90, thresholds) == '#4CAF50'

    def test_value_at_highest_threshold(self):
        thresholds = [(80, '#4CAF50'), (50, '#FFC107')]
        assert get_gauge_color(80, thresholds) == '#4CAF50'

    def test_value_between_thresholds(self):
        thresholds = [(80, '#4CAF50'), (50, '#FFC107')]
        assert get_gauge_color(65, thresholds) == '#FFC107'

    def test_value_at_lower_threshold(self):
        thresholds = [(80, '#4CAF50'), (50, '#FFC107')]
        assert get_gauge_color(50, thresholds) == '#FFC107'

    def test_value_below_all_thresholds(self):
        thresholds = [(80, '#4CAF50'), (50, '#FFC107')]
        assert get_gauge_color(30, thresholds) == '#FFC107'

    def test_single_threshold(self):
        thresholds = [(50, '#4CAF50')]
        assert get_gauge_color(60, thresholds) == '#4CAF50'
        assert get_gauge_color(40, thresholds) == '#4CAF50'


# ── get_health_gauge_color ─────────────────────────────────────────────────


class TestGetHealthGaugeColor:
    def test_green_at_80(self):
        assert get_health_gauge_color(80) == '#4CAF50'

    def test_green_at_100(self):
        assert get_health_gauge_color(100) == '#4CAF50'

    def test_yellow_at_79(self):
        assert get_health_gauge_color(79) == '#FFC107'

    def test_yellow_at_50(self):
        assert get_health_gauge_color(50) == '#FFC107'

    def test_red_at_49(self):
        assert get_health_gauge_color(49) == '#F44336'

    def test_red_at_0(self):
        assert get_health_gauge_color(0) == '#F44336'


# ── get_bandwidth_gauge_color ──────────────────────────────────────────────


class TestGetBandwidthGaugeColor:
    def test_green_at_0(self):
        assert get_bandwidth_gauge_color(0) == '#4CAF50'

    def test_green_at_60(self):
        assert get_bandwidth_gauge_color(60) == '#4CAF50'

    def test_yellow_at_61(self):
        assert get_bandwidth_gauge_color(61) == '#FFC107'

    def test_yellow_at_80(self):
        assert get_bandwidth_gauge_color(80) == '#FFC107'

    def test_red_at_81(self):
        assert get_bandwidth_gauge_color(81) == '#F44336'

    def test_red_at_100(self):
        assert get_bandwidth_gauge_color(100) == '#F44336'


# ── compute_scorecard_score ────────────────────────────────────────────────


class TestComputeScorecardScore:
    def test_perfect_scores_returns_100(self):
        score = compute_scorecard_score(100, 100, 100, 100)
        assert score == pytest.approx(100.0)

    def test_all_zeros_returns_0(self):
        score = compute_scorecard_score(0, 0, 0, 0)
        assert score == pytest.approx(0.0)

    def test_weighted_formula(self):
        # uptime=80*0.40 + signal=60*0.25 + incident=40*0.20 + bandwidth=20*0.15
        # = 32 + 15 + 8 + 3 = 58
        score = compute_scorecard_score(80, 60, 40, 20)
        assert score == pytest.approx(58.0)

    def test_uptime_has_highest_weight(self):
        # Only uptime at 100, rest at 0
        score = compute_scorecard_score(100, 0, 0, 0)
        assert score == pytest.approx(40.0)

    def test_returns_float(self):
        score = compute_scorecard_score(75, 85, 65, 55)
        assert isinstance(score, float)

    def test_result_in_range(self):
        score = compute_scorecard_score(50, 50, 50, 50)
        assert 0 <= score <= 100


# ── score_to_grade ─────────────────────────────────────────────────────────


class TestScoreToGrade:
    def test_grade_a_at_100(self):
        assert score_to_grade(100) == 'A'

    def test_grade_a_at_90(self):
        assert score_to_grade(90) == 'A'

    def test_grade_b_at_89(self):
        assert score_to_grade(89) == 'B'

    def test_grade_b_at_80(self):
        assert score_to_grade(80) == 'B'

    def test_grade_c_at_79(self):
        assert score_to_grade(79) == 'C'

    def test_grade_c_at_70(self):
        assert score_to_grade(70) == 'C'

    def test_grade_d_at_69(self):
        assert score_to_grade(69) == 'D'

    def test_grade_d_at_60(self):
        assert score_to_grade(60) == 'D'

    def test_grade_f_at_59(self):
        assert score_to_grade(59) == 'F'

    def test_grade_f_at_0(self):
        assert score_to_grade(0) == 'F'

    def test_boundary_89_point_9(self):
        assert score_to_grade(89.9) == 'B'

    def test_boundary_90_point_0(self):
        assert score_to_grade(90.0) == 'A'


# ── filter_nonzero_segments ────────────────────────────────────────────────


class TestFilterNonzeroSegments:
    def test_filters_out_zeros(self):
        result = filter_nonzero_segments({"iOS": 5, "Android": 0, "Windows": 3})
        assert result == {"iOS": 5, "Windows": 3}

    def test_all_nonzero_returns_all(self):
        data = {"iOS": 5, "Android": 2, "Windows": 3}
        assert filter_nonzero_segments(data) == data

    def test_all_zero_returns_empty(self):
        assert filter_nonzero_segments({"iOS": 0, "Android": 0}) == {}

    def test_empty_dict_returns_empty(self):
        assert filter_nonzero_segments({}) == {}

    def test_single_nonzero_entry(self):
        assert filter_nonzero_segments({"iOS": 1}) == {"iOS": 1}

    def test_single_zero_entry(self):
        assert filter_nonzero_segments({"iOS": 0}) == {}


# ── get_signal_bar_data ────────────────────────────────────────────────────


class TestGetSignalBarData:
    def test_mesh_quality_5_all_filled_green(self):
        result = get_signal_bar_data(5)
        assert result == {"filled": 5, "unfilled": 0, "color": "#4CAF50"}

    def test_mesh_quality_4_green(self):
        result = get_signal_bar_data(4)
        assert result == {"filled": 4, "unfilled": 1, "color": "#4CAF50"}

    def test_mesh_quality_3_yellow(self):
        result = get_signal_bar_data(3)
        assert result == {"filled": 3, "unfilled": 2, "color": "#FFC107"}

    def test_mesh_quality_2_yellow(self):
        result = get_signal_bar_data(2)
        assert result == {"filled": 2, "unfilled": 3, "color": "#FFC107"}

    def test_mesh_quality_1_red(self):
        result = get_signal_bar_data(1)
        assert result == {"filled": 1, "unfilled": 4, "color": "#F44336"}

    def test_mesh_quality_below_1_clamps_to_1(self):
        result = get_signal_bar_data(0)
        assert result == {"filled": 1, "unfilled": 4, "color": "#F44336"}

    def test_mesh_quality_negative_clamps_to_1(self):
        result = get_signal_bar_data(-5)
        assert result == {"filled": 1, "unfilled": 4, "color": "#F44336"}

    def test_mesh_quality_above_5_clamps_to_5(self):
        result = get_signal_bar_data(10)
        assert result == {"filled": 5, "unfilled": 0, "color": "#4CAF50"}

    def test_filled_plus_unfilled_always_5(self):
        for mq in range(1, 6):
            result = get_signal_bar_data(mq)
            assert result["filled"] + result["unfilled"] == 5


# ── check_firmware_consistency ─────────────────────────────────────────────


class TestCheckFirmwareConsistency:
    def test_all_identical_returns_true(self):
        assert check_firmware_consistency(["7.3.0-677", "7.3.0-677", "7.3.0-677"]) is True

    def test_mixed_versions_returns_false(self):
        assert check_firmware_consistency(["7.3.0-677", "7.2.0-500", "7.3.0-677"]) is False

    def test_single_element_returns_true(self):
        assert check_firmware_consistency(["7.3.0-677"]) is True

    def test_empty_list_returns_true(self):
        assert check_firmware_consistency([]) is True

    def test_two_identical_returns_true(self):
        assert check_firmware_consistency(["1.0", "1.0"]) is True

    def test_two_different_returns_false(self):
        assert check_firmware_consistency(["1.0", "2.0"]) is False
