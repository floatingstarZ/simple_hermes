# Simple Hermes Design Notes

This project is meant to be the smallest useful teaching version of Hermes.

Core ideas preserved
- An agent object owns the execution loop
- The agent has an explicit planning layer (`PlannerDecision`, `ToolCall`)
- Tools live behind a registry
- General durable memory persists independently from user profile memory
- Session history persists independently from memory
- A tiny compression step demonstrates continuity
- The CLI is a surface, not the core logic
- A real LLM backend can optionally replace the rule-based planner
- A Hermes runtime bridge can optionally reuse the full Hermes auth/provider layer

Big things intentionally omitted
- Browser automation
- Gateway / ACP / full Cron
- MCP
- Subagents
- Production-grade context compression
- Provider fallback orchestration as a full system

Why keep rule-based planning when a real backend exists?
Because the point is educational clarity.
You can first understand the architecture from deterministic logic, then switch on the real backend when you want more realism.

Why add the Hermes runtime bridge?
Because it lets this tiny project reuse real credentials and provider setup from the main Hermes installation.
That makes the teaching project practical without forcing it to reimplement all of Hermes auth/runtime complexity.

Why split memory into two stores?
Because full Hermes distinguishes:
- durable facts about the world/project/environment
- durable facts about the user

That separation is one of the easiest architecture lessons to carry over from the real project.

What the tiny compression step teaches
The compression logic here is intentionally tiny, but it demonstrates the key continuity idea:
- long conversations eventually need summarization
- summary becomes part of the running state

How to extend it
- Add more tools in `simple_hermes/tools.py`
- Expand `simple_hermes/backend.py` to support more provider styles
- Expand `simple_hermes/session.py` with richer metadata or FTS search
- Replace the tiny compression with a clearer session-summary model
- Add a delegated child-agent experiment as the next educational step
