"""
Risk identification: AI-suggested ratings are generated when risk
identification runs, an INDICATIVE score counts them without making them
official, and an analyst saves all ratings in one request.
"""

from tests.conftest import ok


def _factors(client, auth, aid):
    return [
        f
        for f in ok(client.get(f"/api/assessments/{aid}/risk-factors", headers=auth("analyst")))
        if f["applicable"] and not f["excluded"]
    ]


def _indicative(client, auth, aid):
    return ok(client.get(f"/api/assessments/{aid}/risk-factors/indicative-score", headers=auth("analyst")))


def test_running_risk_identification_leaves_suggestions_and_an_indicative_score(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    factors = _factors(client, auth, aid)
    assert factors
    # Suggested for every factor without the analyst clicking "Suggest ratings".
    assert all(f["ai_suggested_likelihood"] is not None for f in factors)
    # ...but none is rated: the suggestion is not a rating.
    assert all(f["likelihood"] is None for f in factors)

    score = _indicative(client, auth, aid)
    assert score["suggestions_used"] == len(factors) and score["confirmed_ratings"] == 0
    assert score["indicative_score"] is not None and score["indicative_band"]
    # The official calculation is unchanged: nothing is rated, so still provisional.
    assert score["official_is_provisional"] is True and score["official_score"] is None


def test_save_all_ratings_confirms_the_suggestions_in_one_request(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    factors = _factors(client, auth, aid)
    indicative = _indicative(client, auth, aid)

    saved = ok(
        client.patch(
            f"/api/assessments/{aid}/risk-factors/ratings",
            json={
                "ratings": [
                    {"risk_factor_id": f["id"], "likelihood": f["ai_suggested_likelihood"], "impact": f["ai_suggested_impact"]}
                    for f in factors
                ]
            },
            headers=auth("analyst"),
        )
    )
    assert len(saved) == len(factors)
    assert {f["rating_source"] for f in saved} == {"ANALYST_CONFIRMED"}

    after = _indicative(client, auth, aid)
    assert after["suggestions_used"] == 0 and after["confirmed_ratings"] == len(factors)
    # Confirming the suggestions makes the official score what was indicated.
    assert after["official_is_provisional"] is False
    assert after["official_score"] == indicative["indicative_score"]
    assert after["official_band"] == indicative["indicative_band"]


def test_a_rating_that_differs_from_the_suggestion_needs_a_reason_and_nothing_is_saved(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    factors = _factors(client, auth, aid)
    first, *rest = factors
    differing = 1 if first["ai_suggested_likelihood"] != 1 else 2

    ratings = [{"risk_factor_id": first["id"], "likelihood": differing, "impact": first["ai_suggested_impact"]}]
    ratings += [
        {"risk_factor_id": f["id"], "likelihood": f["ai_suggested_likelihood"], "impact": f["ai_suggested_impact"]}
        for f in rest
    ]
    refused = client.patch(
        f"/api/assessments/{aid}/risk-factors/ratings", json={"ratings": ratings}, headers=auth("analyst")
    )
    assert refused.status_code == 422, refused.text
    errors = refused.json()["detail"]["errors"]
    assert [e["risk_factor_id"] for e in errors] == [first["id"]]
    # All or nothing: the valid ratings were not saved either.
    assert all(f["likelihood"] is None for f in _factors(client, auth, aid))

    ratings[0]["reason"] = "Volumes are lower than the model assumed."
    saved = ok(
        client.patch(f"/api/assessments/{aid}/risk-factors/ratings", json={"ratings": ratings}, headers=auth("analyst"))
    )
    by_id = {f["id"]: f for f in saved}
    assert by_id[first["id"]]["rating_source"] == "ANALYST_OVERRIDE"


def test_only_pipeline_roles_can_save_ratings_in_bulk(client, auth, analysed_assessment):
    aid = analysed_assessment()["id"]
    factor = _factors(client, auth, aid)[0]
    body = {"ratings": [{"risk_factor_id": factor["id"], "likelihood": 3, "impact": 3}]}
    assert client.patch(f"/api/assessments/{aid}/risk-factors/ratings", json=body, headers=auth("owner")).status_code == 403
