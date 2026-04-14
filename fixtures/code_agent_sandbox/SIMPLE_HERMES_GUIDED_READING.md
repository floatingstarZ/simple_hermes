# Simple Hermes Guided Reading

This document explains how to read the small teaching project step by step.

Goal
- Understand the architecture quickly
- See how the small project maps to the full Hermes ideas
- Know which file to open first, second, and third

1. Start with README.md
Why:
- It tells you the scope of the project
- It explains what is intentionally missing
- It now also explains the difference between rule-based mode and real backend mode

2. Open simple_hermes/cli.py
Why:
- This is the easiest entry point
- You see that the CLI is only a shell around the agent
- You also see that backend mode is just configuration, not the core loop itself

What to notice:
- `main()` creates `SimpleAgent`
- user input is forwarded to `agent.run()`
- output is printed directly

3. Open simple_hermes/agent.py
Why:
- This is the most important file in the small project
- It corresponds to Hermes' `AIAgent`

Read in this order:
- `ToolCall`
- `PlannerDecision`
- `AgentResponse`
- `SimpleAgent.__init__()`
- `_plan_tool()`
- `plan()`
- `_maybe_compress_history()`
- `run()`

What to notice:
- `plan()` now has two modes: explicit command routing and optional backend-driven planning
- `run()` appends user history, executes tools, stores assistant output
- the mini compression step shows the basic idea of continuity

4. Open simple_hermes/backend.py
Why:
- This is the bridge from teaching toy to real model-backed agent
- It shows how an OpenAI-compatible backend can be used without changing the rest of the architecture

What to notice:
- backend interface
- OpenAICompatibleBackend
- JSON planner response parsing
- env-based backend selection

5. Open simple_hermes/prompting.py
Why:
- This is the tiny version of Hermes prompt building
- It shows how memory + history + tools become model context

6. Open simple_hermes/tools.py
Why:
- This is the tiny version of the Hermes tool platform

Read in this order:
- `Tool`
- `ToolRegistry`
- `BuiltInTools`
- `_register_tools()`

What to notice:
- tools are registered, not hardcoded in the agent loop
- adding a new tool means registering a new handler
- this is the key teaching bridge to full Hermes registry design

7. Open simple_hermes/memory.py
Why:
- This shows the split memory idea very clearly

What to notice:
- general memory and user memory are separate
- both persist on disk
- `as_prompt_block()` merges them into a model-facing block

That mirrors the full Hermes idea that memory and user profile are distinct.

8. Open simple_hermes/session.py
Why:
- This is the tiny version of SessionDB

What to notice:
- sessions and messages are stored in SQLite
- history and search are separate from memory
- this helps you understand why memory != history

9. Run tests while reading
Command:
- `python3 -m unittest discover -s tests -v`

Why:
- the tests show what behavior is considered important
- they also show the intended API of the project

Suggested test reading order:
- `tests/test_agent.py`
- `tests/test_tools.py`
- `tests/test_memory.py`
- `tests/test_session.py`
- `tests/test_compression.py`

10. Finally compare with the full Hermes docs
After you understand the small project, go back to:
- `M_docs/docs/hermes-beginner-guide.md`
- `M_docs/docs/hermes-intermediate-tutorial.md`
- `M_docs/docs/hermes-expert-tutorial.md`

Then compare diagrams:
- `simple-hermes-architecture.svg`
- `simple-hermes-request-sequence.svg`
- `simple-hermes-vs-full-hermes.svg`

What this teaches you
- In the small project, everything is easy to trace
- In full Hermes, the same concepts exist, but there are more layers and more robustness mechanisms
- Once you understand the small shape, the big project becomes much less intimidating

One-sentence reading strategy
- CLI shows the shell
- Agent shows the brain
- Backend/prompting show how model context is built
- Tools show the action system
- Memory shows durable facts
- Session shows historical continuity
