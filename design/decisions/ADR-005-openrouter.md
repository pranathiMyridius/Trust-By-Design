# ADR-005 — OpenRouter as model gateway, with a pinned model

**Status:** Accepted (retain; harden) · **Date:** 2026-09-30

**Context.** All AI calls go through `metered_post()` to OpenRouter; model from `OPENROUTER_MODEL`, default `openrouter/free`. Evals with `openrouter/free` errored on 18 of 21 cases (random model per call, rate limits, invalid JSON).

**Problem.** Keep a single gateway and make model choice reproducible and governed.

**Decision.** Keep OpenRouter as the single gateway behind the `metered_post` interface. **Pin** one model per task via configuration; refuse `openrouter/free` and any model not in `OPENROUTER_ALLOWED_MODELS` when `APP_ENV ∈ {staging, prod}`. Record requested and served model on every call and run. Prefer providers with zero-retention/no-training policies.

**Alternatives considered.** Direct provider SDKs (one integration per vendor; loses easy switching); multiple gateways (complexity); self-hosted models (infrastructure, quality).

**Why selected.** Already integrated; one key; model switch is configuration; metering and masking already wrap it.

**Benefits.** Configurable model, one audit point, cost tracking. **Trade-offs.** Extra hop and third-party dependency; served model can differ from requested (hence recording `response_model`).

**Consequences.** Model changes follow the eval gate in `ai/ai-governance.md` §3; production may swap the gateway URL for an enterprise endpoint without code changes.
