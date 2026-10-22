"""Validate completeness without filling in missing match statistics."""

from personal_stats import clean_stats


def missing_fields(stats, required=("xG", "shots", "shots_on_target")):
    checked = clean_stats(stats)
    missing = []
    for metric in required:
        pair = checked.get(metric)
        for side in ("home", "away"):
            if pair is None or pair[side] is None:
                missing.append(f"{metric}.{side}")
    return missing
