# AI orchestration

The risk-generation path is a LangGraph state machine
(`app/langgraph/graph.py`), run via `app/langgraph/service.py`:

    START
      -> load_assessment
      -> gather_intelligence
      -> identify_risks      (AI)
      -> calculate_scores    (deterministic)
      -> persist_results
      -> END

## Provider

All AI calls go to the provider configured in `app/ai/provider.py`:
**OpenAI** (`https://api.openai.com/v1`, `LLM_PROVIDER=openai`, the default
when `OPENAI_API_KEY` is set) or **OpenRouter** (`https://openrouter.ai/api/v1`).
Both are OpenAI-compatible, so only the base URL, key and model names differ.
The OpenAI default chat model is `gpt-4.1-mini` (`OPENAI_MODEL`); every call
sets a temperature and JSON mode, so choose a non-reasoning chat model.
Embeddings use `text-embedding-3-small` (1536 dimensions). Earlier revisions
of this document described Gemini and Anthropic directly -- neither is
called any more.

The deterministic test suites never use the configured provider: they pin
the offline configuration and fake every call at `metered_post()`. Live
evaluations (`tests/evals`) opt in with `LIVE_LLM=1`.

Every outbound call goes through `metered_post()`
(`app/ai/metering.py`), which records tokens, cost, latency and outcome
to `AIUsageLog`. There are no un-metered call sites.

## Split between AI and deterministic logic

The AI produces the six per-dimension inherent-risk results:

    CUSTOMER  OPERATIONAL  FINANCIAL  COMPLIANCE  TECHNOLOGY  THIRD_PARTY

each with `dimension`, `score` (0-100), `severity` and `reason`.

The application then calculates the overall score and risk level using
the configured weights and thresholds (`app/risk_engine/scoring.py`,
overridable per `RiskMethodology`). Aggregation stays reproducible while
the risk-factor judgement is AI-driven.

## When the AI fails

`app/risk_engine/degraded.py` defines three explicit modes -- see that
module for the full contract:

  * `ai_assisted` -- normal scoring, finalizable.
  * `rules_only`  -- deterministic rule engine only; provisional, needs
                     reviewer acknowledgement.
  * `unavailable` -- no risk rating is written at all.

A failed AI step never produces a successful-looking low-risk score.

## Configuration

`backend/.env` (see `.env.example`):

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
# or LLM_PROVIDER=openrouter with OPENROUTER_API_KEY / OPENROUTER_MODEL
OPENROUTER_TIMEOUT_SECONDS=120
```

Do not commit real API keys.

## API contract

    POST /api/assessments/{assessment_id}/analyze
