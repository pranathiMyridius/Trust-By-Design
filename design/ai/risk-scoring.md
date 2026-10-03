# Risk Scoring Architecture

**Principle:** every number and band of record is produced by deterministic Python from stored inputs and a fingerprinted methodology. The LLM supplies *candidates* (applicability, indicators, evidence) and *suggestions* (L×I) only. This is already how the code works (`risk_engine/scoring.py`, `services/inherent_risk_service.py`, `control_engine/`, `services/residual_risk_service.py`); this document formalises it and closes the gaps.

Diagram: `diagrams/risk-scoring.mmd`.

## 1. Vocabulary

| Term | Definition | Stored in |
|---|---|---|
| **Risk dimension** (category) | One of 10 canonical categories; `CONTROL_ENVIRONMENT_RISK` is a *mitigant* category (excluded from inherent average) | code list + methodology weights |
| **Risk factor** | An assessment's instance of a category: applicable?, indicators, evidence, rating | `risk_factors` |
| **Indicator** | Specific FC red flag (12 today, e.g. `SANCTIONS_EXPOSURE`, `CASH_ACCESS`); only accepted with a verified quote | `risk_factors.indicators` |
| **Crime typology** | AML / TF / SANCTIONS / FRAUD / BRIBERY_CORRUPTION tag on a factor (reporting + explanation; **no scoring effect in v1**) | `risk_factors.crime_typologies` |
| **Factor score** | `L × I` normalised to 0–100 | `risk_factors.score` |
| **Dimension score** | = factor score (one factor per category per assessment) | same |
| **Inherent score / band** | Weighted average of rated factor scores → band, then escalation floors | `inherent_risk_calculations` |
| **Control effectiveness** | Per-factor rating WEAK / PARTIAL / EFFECTIVE from control assessments | `residual_risk_calculations.control_ratings` |
| **Residual band** | Grid lookup (inherent band × weakest control rating), then non-mitigable floors | `residual_risk_calculations` |
| **Overall risk (of record)** | Residual band, once frozen; before that, the inherent band is shown as "inherent" | `assessments.residual_risk_level` |
| **Confidence** | Deterministic measure of how complete and evidenced the result is (§6) — never an LLM self-rating | `inherent_risk_calculations.confidence_level` |
| **Evidence** | Verified verbatim quotes with source reference (see `evidence-and-explainability.md`) | `risk_factors.evidence` |

## 2. Scales (default methodology; configurable per version)

| Value | Likelihood | Impact |
|---|---|---|
| 1 | Rare | Negligible |
| 2 | Unlikely | Minor |
| 3 | Possible | Moderate |
| 4 | Likely | Major |
| 5 | Almost certain | Severe |

## 3. Formulas (engine version `1.0.0` — as implemented)

**Factor score**
```
max_raw      = max(likelihood_scale) × max(impact_scale)        # 25
factor_score = round( clamp( (L × I) / max_raw × 100, 0, 100 ), 2 )
```

**Eligible factors**: `applicable AND NOT excluded AND category ∉ mitigant_categories`.
**Rated**: analyst-confirmed `likelihood` and `impact` are both non-null (AI suggestions never count).

**Inherent score**
```
weighted_sum = Σ (factor_score_i × w_i)   over rated eligible factors
total_weight = Σ w_i                      over rated eligible factors
inherent_score = round(weighted_sum / total_weight, 2)   if total_weight > 0
               = None                                     if eligible factors exist but none rated
               = 0.0                                      if no eligible factors (a genuine "no inherent risk" finding)
is_provisional = any eligible factor unrated
```
Default weights: 0.10 for each of the 10 categories (effective 1/9 each after excluding the mitigant).

**Band** (`determine_risk_band`): highest band whose `min ≤ score`.

| Band | Range |
|---|---|
| LOW | min 0 (stored max 39) |
| MEDIUM | min 40 (max 59) |
| HIGH | min 60 (max 79) |
| CRITICAL | min 80 (max 100) |

Lookup uses `min` only, so e.g. 59.5 → MEDIUM and 79.99 → HIGH; `max` is used only to clamp the indicative residual score. Engine 1.1.0 validates that bands are contiguous.

If `inherent_score is None` → band `UNRATED` (never LOW).

**Escalation rules** (only `status = approved` rules apply; each raises band to at least `min_band`):

| Rule | Condition | Min band | Mandatory review | Non-mitigable | Default status |
|---|---|---|---|---|---|
| `SANCTIONS_EXPOSURE_001` | accepted indicator `SANCTIONS_EXPOSURE` (verified, on-topic quote) | CRITICAL | ✔ | ✔ | approved |
| `MIN_BAND_KEY_FACTOR_001` | rated GEOGRAPHIC / CUSTOMER_SEGMENT / OWNERSHIP factor ≥ HIGH | HIGH | ✔ | – | draft |
| `GEO_FATF_CALL_FOR_ACTION_001` | attested jurisdiction tier CALL_FOR_ACTION | CRITICAL | ✔ | ✔ | draft |
| `GEO_HIGH_RISK_THIRD_COUNTRY_001` | attested tier INCREASED_MONITORING or EU high-risk tiers | HIGH | ✔ | – | draft |

Recommendation for the production methodology (open question Q-04): approve the two GEO rules so FATF/EU designations affect the band, not only challenge findings.

**Control effectiveness** (per factor)
```
points(control) = {EFFECTIVE:4, PARTIALLY_EFFECTIVE:2, INEFFECTIVE:0, UNVERIFIED:0}
                  EFFECTIVE is downgraded to PARTIALLY_EFFECTIVE if no evidence, incomplete coverage, or depends on unavailable data
factor_points   = max(points over the factor's controls)          (0 if none)
factor_rating   = EFFECTIVE if ≥4, PARTIAL if ≥2, else WEAK
overall_rating  = weakest factor_rating across eligible factors
```

**Residual band**
```
grid_band     = residual_grid[inherent_band][overall_rating]      # never above inherent (validated)
residual_band = max(grid_band, min_band of every triggered non_mitigable rule)
residual_score (indicative) = clamp(max(0, inherent_score − min(Σ points, 30)), band.min, band.max)
                              — suppressed (None) when a floor or override set the band
```
Default grid v1.0:

| Inherent \ Controls | WEAK | PARTIAL | EFFECTIVE |
|---|---|---|---|
| LOW | LOW | LOW | LOW |
| MEDIUM | MEDIUM | MEDIUM | LOW |
| HIGH | HIGH | HIGH | MEDIUM |
| CRITICAL | CRITICAL | CRITICAL | HIGH |

Residual cannot be computed while inherent is `UNRATED` or provisional (unless overridden).

### Worked example

Change: cross-border P2P wallet, remote onboarding. Analyst ratings: PRODUCT 3×3=36, CUSTOMER 4×4=64, GEOGRAPHIC 4×3=48, CHANNEL 4×4=64, TRANSACTION 3×4=48; others not applicable. Weights 0.10 each.
`inherent = (36+64+48+64+48)×0.1 / 0.5 = 52.0 → MEDIUM`. AI proposed `SANCTIONS_EXPOSURE` but the quote was "usable internationally" → rejected (no topic cue) → no floor. Controls: weakest factor PARTIAL → grid MEDIUM/PARTIAL → **residual MEDIUM**, indicative score `clamp(52 − 12, 40, 59) = 40.0`.

## 4. Missing-data behaviour

| Situation | Behaviour |
|---|---|
| Eligible factor unrated | Excluded from average; calc `is_provisional = true`; UI shows **UNRATED** badge (never LOW/0 — fixes D-15); manager approval blocked |
| All eligible factors unrated | Score `None`, band `UNRATED` |
| Factor with weight 0 rated | **Fix (engine 1.1.0):** treat as provisional and raise `VALIDATION_FAILED` at methodology activation (weights of non-mitigant categories must be > 0) |
| Evidence missing for an applicable AI factor | `evidence_status = INSUFFICIENT_EVIDENCE`; factor kept (analyst decides); lowers confidence; challenge finding `UNSUPPORTED_CONCLUSION` |
| Category omitted by the model | Filled as not applicable with note "model did not assess"; listed in run `output_summary`; challenge `MISSING_RISK` |
| Rules-only run | All factors unrated; `unevaluated_categories` listed; confidence capped at LOW; acknowledgement required |
| Unresolved country text | No designation match; challenge finding `UNRESOLVED_COUNTRY_REFERENCE` |
| Unattested reference snapshot | Not used for scoring; reported under `snapshots_skipped` |

## 5. Conflict handling

| Conflict | Rule |
|---|---|
| Sources contradict each other (model flags `conflicting_evidence`) | `evidence_status = CONFLICTING_EVIDENCE`; HIGH challenge finding `CONTRADICTION`; blocks manager approval until resolved/accepted |
| Form field vs extracted document value (`consistency_check`) | Challenge `CONTRADICTION` with both values |
| AI suggestion vs analyst rating | Analyst wins; `rating_source = ANALYST_OVERRIDE`; reason required; both stored |
| AI applicability vs analyst | Analyst excludes (reason, `excluded_by_id`) or adds MANUAL factor |
| Calculated band vs override | Override wins for display and residual; calculated value preserved on the same row; see §7 |
| Rule floor vs grid / override | Floors of non-mitigable rules always win |

## 6. Confidence (new, deterministic)

```
rating_coverage   = rated eligible / eligible                              (1.0 if none eligible)
evidence_coverage = eligible AI/RULES factors with EVIDENCE_FOUND
                    / eligible AI/RULES factors                             (1.0 if none; MANUAL factors excluded)
conflicts         = count(evidence_status == CONFLICTING_EVIDENCE)

confidence_level =
  LOW    if assessment_mode != ai_assisted  or rating_coverage < 1.0  or conflicts > 0
  HIGH   if evidence_coverage ≥ 0.80
  MEDIUM if evidence_coverage ≥ 0.50
  LOW    otherwise
```
Confidence **never changes the score or band**. It is displayed next to the band, stored on the calculation, frozen into the decision record, and `LOW` triggers the existing `LOW_CONFIDENCE` challenge (today triggered only by `is_provisional`).

## 7. Human override rules

| Rule | Enforcement |
|---|---|
| Who | `override.create` (FCRM_ANALYST, MANAGER, ADMIN) |
| What can be overridden | Factor rating (via re-rate), factor applicability (exclude/add), inherent band/value, control assessment outcome, residual band (manager+ only) |
| Reason | Required, ≥ 20 chars; optional comment |
| Record | New calc version keeps `calculated_score/band` and `override_*`; `assessment_overrides` row with `ai_value` (or calculated value), `human_value`, `previous_value_source`, `overridden_by_id`, timestamp; audit with before/after |
| Floors | Cannot override below a triggered non-mitigable floor (`409 NON_MITIGABLE_FLOOR`) |
| Downward overrides | Lowering a band by ≥1 creates HIGH finding `OVERRIDE_REDUCED_RISK` → requires manager acceptance (four-eyes) |
| After decision | No overrides once status ∈ FINAL_DECISION_STATUSES (existing `decision_lock`) |
| Never silent | Recalculation after an override keeps the override on the new version and shows both values |

## 8. Versioning

| Item | Identifier | Where recorded |
|---|---|---|
| Methodology (weights, scales, bands, rules, approvals, mitigants, grid) | `methodology_version` (`v{n}`) + `methodology_fingerprint` (SHA-256 of config) | every inherent/residual calc; decision record |
| Scoring engine code | `SCORING_ENGINE_VERSION` constant in `risk_engine/scoring.py` (start `1.0.0`; `1.1.0` with the weight-0 fix) | every calc (new column) |
| Calculation instance | `inherent_risk_calculations.version` / `is_current` | row |
| Inputs | `inputs` JSON + `inputs_sha256` | row |
| Reference data | snapshot ids + checksums in `reference_data` | row |

Methodology rows are locked on first use (existing); changes require clone → edit → activate (ADMIN, reason). The unused `thresholds` column is dropped from the config read path (D-10), and bands must be contiguous and cover 0–100 (validated at activation).

## 9. Answering "Why did this assessment receive this score?"

`GET /api/assessments/{id}/scores` + `GET /api/assessments/{id}/explain` return, for the current calculation:

1. Band and score, provisional flag, confidence.
2. Each eligible factor: category, L, I, factor score, weight, contribution (`score × w / Σw`), `rating_source`, who rated and when, AI suggestion shown alongside.
3. Rules fired: rule code/version, triggering indicator or jurisdiction, band before → after.
4. Evidence per factor: verified quotes with source, doc version, offset; rejected quotes/indicators with reasons; missing information.
5. Overrides: previous value, new value, reason, user, time.
6. Methodology id/version/fingerprint, scoring engine version, analysis run (model, prompt version).
7. Residual: control rating per factor, grid cell, floors.

**Reproduction test:** `score_replay(calc_id)` recomputes from `inputs` + snapshotted weights/bands/rules with the recorded engine version and must return identical `final_score`, `risk_band`, `triggered_rules`. Run in the test suite for fixtures and available to ADMIN as `GET /api/system/integrity` check.
