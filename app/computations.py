"""
Computation functions for dashboard visual enhancements.

Pure functions for health scores, gauge colors, scorecard grades,
and other derived metrics. No external dependencies.
"""


def compute_health_score(green_nodes, total_nodes, avg_signal_dbm, uptime_24h, bandwidth_utilization):
    """Compute a composite health score (0–100) from network metrics.

    Args:
        green_nodes: Number of eero nodes with green (healthy) status.
        total_nodes: Total number of eero nodes in the network.
        avg_signal_dbm: Average signal strength in dBm (typically -90 to -30).
        uptime_24h: Uptime percentage over the last 24 hours (0–100).
        bandwidth_utilization: Bandwidth utilization percentage (0–100).

    Returns:
        Integer health score clamped to [0, 100].
    """
    # Node score: percentage of healthy nodes
    node_score = (green_nodes / total_nodes) * 100 if total_nodes > 0 else 0

    # Signal score: map -90 dBm → 0, -30 dBm → 100
    signal_score = max(0, min(100, ((avg_signal_dbm + 90) / 60) * 100))

    # Uptime score: already 0–100
    uptime_score = uptime_24h

    # Bandwidth score: headroom (inverted utilization)
    bandwidth_score = 100 - bandwidth_utilization

    # Weighted average
    health_score = round(
        node_score * 0.25
        + signal_score * 0.25
        + uptime_score * 0.25
        + bandwidth_score * 0.25
    )

    return max(0, min(100, health_score))


def get_gauge_color(value, thresholds):
    """Return a hex color string based on value and threshold list.

    Args:
        value: Numeric value to evaluate.
        thresholds: List of (threshold, color) tuples sorted descending.
            Returns the color for the first threshold the value meets or exceeds.
            Returns the last color if value is below all thresholds.

    Returns:
        Hex color string (e.g. '#4CAF50').
    """
    for threshold, color in thresholds:
        if value >= threshold:
            return color
    # Below all thresholds — return the last color
    return thresholds[-1][1]


def get_health_gauge_color(score):
    """Return gauge color for a health score.

    score >= 80 → green (#4CAF50)
    score >= 50 → yellow (#FFC107)
    else → red (#F44336)
    """
    return get_gauge_color(score, [
        (80, '#4CAF50'),
        (50, '#FFC107'),
        (0, '#F44336'),
    ])


def get_bandwidth_gauge_color(utilization):
    """Return gauge color for bandwidth utilization.

    utilization <= 60 → green (#4CAF50)
    utilization <= 80 → yellow (#FFC107)
    else → red (#F44336)
    """
    if utilization <= 60:
        return '#4CAF50'
    elif utilization <= 80:
        return '#FFC107'
    else:
        return '#F44336'


def compute_scorecard_score(uptime_score, signal_score, incident_score, bandwidth_score):
    """Compute a weighted scorecard score (0–100) from individual metric scores.

    Args:
        uptime_score: Uptime percentage score (0–100), weight 0.40.
        signal_score: Signal strength score mapped to 0–100, weight 0.25.
        incident_score: Incident score mapped inversely to 0–100, weight 0.20.
        bandwidth_score: Bandwidth headroom score (0–100), weight 0.15.

    Returns:
        Float weighted score in [0, 100].
    """
    return (
        uptime_score * 0.40
        + signal_score * 0.25
        + incident_score * 0.20
        + bandwidth_score * 0.15
    )


def score_to_grade(score):
    """Convert a numeric score to a letter grade.

    Args:
        score: Numeric score (0–100).

    Returns:
        String letter grade: 'A' (90–100), 'B' (80–89), 'C' (70–79),
        'D' (60–69), 'F' (0–59).
    """
    if score >= 90:
        return 'A'
    elif score >= 80:
        return 'B'
    elif score >= 70:
        return 'C'
    elif score >= 60:
        return 'D'
    else:
        return 'F'


def filter_nonzero_segments(device_type_dict):
    """Filter a device type dictionary to only entries with count > 0.

    Args:
        device_type_dict: Dict mapping device type names to counts
            (e.g. {"iOS": 5, "Android": 0, "Windows": 3}).

    Returns:
        Dict with only entries where count > 0.
    """
    return {k: v for k, v in device_type_dict.items() if v > 0}


def get_signal_bar_data(mesh_quality):
    """Return signal bar rendering data for a given mesh quality.

    Args:
        mesh_quality: Integer 1–5 representing mesh signal quality.
            Values < 1 are treated as 1, values > 5 are treated as 5.

    Returns:
        Dict with keys:
            filled (int): Number of filled bars (1–5).
            unfilled (int): Number of unfilled bars (5 - filled).
            color (str): Hex color — green (#4CAF50) for 4–5,
                yellow (#FFC107) for 2–3, red (#F44336) for 1.
    """
    # Clamp to [1, 5]
    clamped = max(1, min(5, mesh_quality))

    if clamped >= 4:
        color = '#4CAF50'
    elif clamped >= 2:
        color = '#FFC107'
    else:
        color = '#F44336'

    return {"filled": clamped, "unfilled": 5 - clamped, "color": color}


def check_firmware_consistency(version_list):
    """Check whether all firmware versions in a list are identical.

    Args:
        version_list: List of firmware version strings.

    Returns:
        True if the list is empty, has one element, or all elements
        are identical. False otherwise.
    """
    if len(version_list) <= 1:
        return True
    return len(set(version_list)) == 1

