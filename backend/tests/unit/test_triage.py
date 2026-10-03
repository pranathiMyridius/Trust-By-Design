"""
Intake triage (app/services/triage.py): the shell company indicator adds
points and never lets a request be triaged below HIGH.
"""

import pytest

from app.services.triage import SHELL_COMPANY_POINTS, compute_priority

BENIGN = {
    "change_type": "PERIODIC_REASSESSMENT",
    "countries_jurisdictions": "Germany",
    "third_party_vendor_usage": "None",
    "expected_transaction_volume": "50k/month",
    "expected_transaction_value": "EUR 2m/month",
    "technology_process_changes": "",
}

RISKY = {
    "change_type": "NEW_GEOGRAPHY",
    "countries_jurisdictions": "Germany, Poland, Cayman Islands",
    "third_party_vendor_usage": "External processor",
    "expected_transaction_volume": "2 million/month",
    "expected_transaction_value": "EUR 20m/month",
    "technology_process_changes": "New platform",
}


def test_benign_request_is_low_without_the_flag():
    assert compute_priority(BENIGN) == ("LOW", 0)


def test_flag_adds_points_and_floors_at_high():
    level, score = compute_priority({**BENIGN, "shell_company_indicator": True})
    assert score == SHELL_COMPANY_POINTS
    assert level == "HIGH"


def test_flag_can_push_a_high_request_to_urgent():
    base_level, base_score = compute_priority({**BENIGN, "change_type": "NEW_GEOGRAPHY", "technology_process_changes": "x", "third_party_vendor_usage": "Vendor"})
    assert base_level == "HIGH" and base_score == 50
    assert compute_priority(
        {**BENIGN, "change_type": "NEW_GEOGRAPHY", "technology_process_changes": "x",
         "third_party_vendor_usage": "Vendor", "shell_company_indicator": True}
    ) == ("URGENT", 80)


def test_urgent_stays_urgent_and_score_is_capped():
    level, score = compute_priority({**RISKY, "shell_company_indicator": True})
    assert level == "URGENT"
    assert score == 100


@pytest.mark.parametrize("flag", [False, None])
def test_no_or_unanswered_flag_changes_nothing(flag):
    assert compute_priority({**BENIGN, "shell_company_indicator": flag}) == compute_priority(BENIGN)
