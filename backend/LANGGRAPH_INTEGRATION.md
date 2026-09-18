# LangGraph + Claude integration

The assessment analysis endpoint now uses LangGraph as the orchestration
layer and Claude as the AI risk-analysis node.

Workflow:

    START
      -> load_assessment
      -> gather_intelligence
      -> identify_risks (Claude)
      -> calculate_scores (deterministic)
      -> persist_results
      -> END

The important design choice is that Claude produces the six per-dimension
inherent-risk results:

    CUSTOMER
    OPERATIONAL
    FINANCIAL
    COMPLIANCE
    TECHNOLOGY
    THIRD_PARTY

Each result contains:

    dimension
    score (0-100)
    severity
    reason

The application then calculates the overall score and overall risk level
using the existing deterministic weights and thresholds. This keeps the
aggregation reproducible while making the actual risk-factor assessment
AI-driven.

Configuration:

    backend/.env

    ANTHROPIC_API_KEY=...
    CLAUDE_MODEL=claude-sonnet-4-6

Do not commit the real API key to source control.

The frontend API contract is unchanged:
    POST /api/assessments/{assessment_id}/analyze
