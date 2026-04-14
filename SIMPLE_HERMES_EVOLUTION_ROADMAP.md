# Simple Hermes Evolution Roadmap

This document explains how you could grow Simple Hermes step by step without losing its educational value.

Current stage: Teaching Core v2
What it already has
- CLI
- SimpleAgent
- explicit PlannerDecision / ToolCall objects
- ToolRegistry
- split memory (general + user)
- SQLite session history
- tiny educational compression step
- tests
- architecture and sequence diagrams

Stage 1: Better teaching ergonomics
Goal:
Make the current system easier to understand and demo.

Possible improvements:
- add richer command help with examples
- add a `status` command showing memory/session counts
- add comments explaining how each tool maps to full Hermes tools
- add a tiny `session_search` alias that makes the naming closer to the real system

Stage 2: More realistic agent execution
Goal:
Make the execution flow feel more like a real tool-calling agent.

Possible improvements:
- separate planner and executor modules
- represent assistant turns explicitly
- store tool call metadata in session history
- add a tiny "reasoning" field in the session store
- make the planner emit a list of steps instead of a single decision

Stage 3: Better continuity model
Goal:
Move closer to the long-running nature of full Hermes.

Possible improvements:
- keep a session title
- add a lightweight lineage / branch concept
- replace LIKE search with SQLite FTS if desired
- expand compression so it rewrites earlier history into a clearer summary block
- add explicit prompt-building from memory + history

Stage 4: Tiny real model backend
Goal:
Bridge from purely educational rule-based behavior to a small real LLM-backed system.

Possible improvements:
- add optional OpenAI-compatible API support
- let the model choose among the registered tools
- keep the current rule-based planner as a fallback "offline mode"
- make tool schema generation visible and inspectable

Stage 5: Selective Hermes features
Goal:
Teach one production idea at a time without exploding complexity.

Candidate features to add one-by-one:
- simple fallback model support
- a mini delegate_task that spawns another SimpleAgent
- a tiny cron runner for one scheduled task type
- a very small skills system using markdown files

Recommended rule for future growth
Never add 5 big ideas at once.
Add one Hermes concept at a time, keep the code readable, and preserve the project as a teaching artifact.

Best next step
If the goal is education, the best next step is probably:
1. add lightweight prompt construction
2. add a tiny real model backend
3. preserve the rule-based mode for readability

That would let Simple Hermes become both:
- easy to read
- genuinely interactive as a tiny agent
