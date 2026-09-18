# Gemini + LangGraph

The risk-generation path is:

FastAPI -> LangGraph -> load_assessment -> gather_intelligence -> identify_risks -> Gemini -> calculate_scores -> persist_results

Gemini returns exactly six structured banking risk dimensions: CUSTOMER, OPERATIONAL, FINANCIAL, COMPLIANCE, TECHNOLOGY, THIRD_PARTY. Each includes score, severity, and reason. The overall score remains deterministic in the application.

Configure `backend/.env`:

```env
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash
```
