# ADR-001 — React + Vite + TypeScript frontend

**Status:** Accepted (retain existing) · **Date:** 2026-09-30

**Context.** The frontend is React 19.2 + Vite 8 + TypeScript 6 with no router and no UI library (`frontend/package.json`). It works and has Playwright coverage, but navigation is state-based, one component is 8,566 lines, and the API base URL is hard-coded 11 times.

**Problem.** Keep or replace the frontend stack, and what minimum changes make it maintainable and reviewer-friendly.

**Decision.** Keep React + Vite + TS. Add **one** dependency: `react-router` for URL routing. Split `AssessmentWorkflow.tsx` into stage modules; introduce a single typed API client configured by `VITE_API_BASE_URL`. No UI kit or state library is required for MVP.

**Alternatives considered.** Next.js (SSR not needed; adds a server tier); Angular (rewrite); keep state-based navigation (no deep links, refresh loses context); adding TanStack Query + a component library now (useful later, not needed to fix current defects).

**Why selected.** Zero rewrite; team knows it; Vite builds a static bundle that deploys anywhere; the router solves deep links/back/refresh which reviewers need to open a specific finding from a comment.

**Benefits.** Low risk, fast builds, static hosting, typed contracts. **Trade-offs.** Hand-rolled data fetching; component styling remains custom CSS (`App.css` 84 KB).

**Consequences.** Playwright helpers must navigate by URL; `SectionNavigator` and localStorage step memory become secondary to routes.
