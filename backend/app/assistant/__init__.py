"""
Assistant (chatbot), Phase A: read-only.

The assistant answers questions about the assessments the signed-in user
is allowed to see -- summaries, deadlines, risk analysis -- by calling a
small set of read-only tools (assistant/tools.py). It never queries the
database directly and has no tool that changes anything; every tool is
scoped to the caller with the same visibility rules the assessment list
uses (app/api/assessments.py::_scope_assessments_for_user).
"""
