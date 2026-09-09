# AI Quant Terminal — SDLC Documentation

This folder tracks the project through the full software development lifecycle.
Each stage folder contains the artifact(s) for that stage. Artifacts are drafted
one stage at a time and require explicit user approval before the next stage begins.

## Stages

| # | Stage | Folder | Status |
|---|-------|--------|--------|
| 1 | Requirements | [01-requirements](01-requirements/) | Approved (amended 2026-09-09: LLM provider) |
| 2 | Architecture & Design | [02-architecture-design](02-architecture-design/) | Approved (amended 2026-09-09: LLM provider) |
| 3 | Planning | [03-planning](03-planning/) | Approved |
| 4 | Implementation | [04-implementation](04-implementation/) | In progress |
| 5 | Testing | [05-testing](05-testing/) | Approved |
| 6 | Deployment | [06-deployment](06-deployment/) | Approved (amended 2026-09-09: LLM provider) |
| 7 | Maintenance & Operations | [07-maintenance-operations](07-maintenance-operations/) | Approved |

**All 7 SDLC stages approved as of 2026-09-09.** Stage 4 (Implementation)
is now unblocked and in progress.

**Amendment note (2026-09-09):** LLM provider changed from Ollama-only to
**Gemini API free tier (default) with Ollama as an optional local
fallback**, selected behind the existing `LLMClient` MCP-pluggable
boundary. Affected: `01-requirements/requirements.md` §7, all of
`02-architecture-design/architecture.md`'s LLM/deployment sections, and
`06-deployment/deployment.md`'s topology/env-config sections. No other
stage content changed.

## Source material

Artifacts here are derived from the two handoff documents in the repo root:

- `ai_quant_product_manager.md` — product vision, users, MVP scope, roadmap, acceptance criteria
- `ai_quant_software_manager.md` — architecture, tech stack, domain model, milestones, engineering rules

## Approval workflow

1. Draft the artifact for the current stage.
2. Pause and present it to the user for review.
3. Incorporate feedback until approved.
4. Mark the stage "Approved" in this table and move to the next stage.
5. Only after **all** stages are approved does implementation planning
   (sequential, unit-by-unit work breakdown) begin in `04-implementation/`.
