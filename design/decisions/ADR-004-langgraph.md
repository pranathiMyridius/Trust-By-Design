# ADR-004 — LangGraph for the risk-identification workflow only

**Status:** Accepted (retain; extend) · **Date:** 2026-09-30

**Context.** `app/langgraph/graph.py` runs a linear 5-node graph with one LLM node. The brief suggests up to 16 nodes, some named as analyses (AML, sanctions, fraud, bribery, geography).

**Problem.** How much of the process to model in LangGraph, and whether to use multiple agents.

**Decision.** Use LangGraph for the **machine** part of analysis only: a 7-node graph (`validate_input → load_context → identify_risks → verify_evidence → tag_and_aggregate → score_provisional → persist_run`, plus `rules_fallback`) with **one** LLM node. Financial-crime typologies are tags produced in the same call and validated deterministically. Human review, approvals and the lifecycle stay in the application state machine (Postgres), not in LangGraph interrupts. Other AI tasks are single calls, not graphs.

**Alternatives considered.** Multi-agent graph (one agent per typology/dimension) — ~5× cost/latency, cross-agent contradictions, no auditability gain; plain Python function chain — loses typed state, per-node tracing and conditional edges; LangGraph checkpointer + `interrupt()` for human review — duplicates the workflow engine and couples approvals to an orchestration library.

**Why selected.** Already implemented and tested; conditional degraded routing becomes explicit edges; nodes are individually testable and traced.

**Benefits.** Clear AI/deterministic boundary per node; progress reporting per node; versionable graph (`WORKFLOW_VERSION`, `graph_hash`). **Trade-offs.** A library dependency for a mostly linear flow; `langgraph>=0.2.0` is unpinned today — pin it.

**Consequences.** Graph gets its own DB session in the worker; node contracts documented in `ai/langgraph-workflow.md`.
