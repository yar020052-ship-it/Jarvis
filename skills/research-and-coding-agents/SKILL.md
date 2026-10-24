---
name: research-and-coding-agents
description: Use when the user asks JARVIS to do autonomous scientific research, build or debug a software project, use specialist agents, or apply ideas from AI Scientist, OpenHands, or Agent Zero.
---

# Research and coding agent workflows

Use JARVIS's native actions to reproduce useful workflows from research and coding-agent frameworks. Do not claim that an upstream framework is installed or running unless its configured service is actually available.

## Scientific research (AI Scientist-inspired)

1. Turn the question into a small research plan with a question, competing hypotheses, measurable outcomes, and explicit unknowns.
2. Use `web_search` in `research` mode for the evidence review. For questions where independent viewpoints help, use `parallel_subtasks` for up to four bounded analyses, then verify factual claims with primary sources.
3. For a computational experiment, describe the experiment and acceptance criteria first. Use `dev_agent` to create a separate project under its normal project directory. Do not run generated code, install packages, access GPUs, or launch long experiments without the user's request and any confirmation required by the relevant JARVIS action.
4. Inspect observed outputs before drawing conclusions. Separate measured results from model-generated interpretation, note failed runs, and report limitations.
5. Present a concise report in the conversation. JARVIS has PDF reading tools, but no general-purpose PDF paper-generation action; create a saved artifact only through a capability that is actually available. Do not claim peer review or scientific novelty based only on an LLM review.

The upstream AI Scientist repository is a separate research system with template-based ML experiments and substantial Linux/CUDA/PyTorch requirements. JARVIS's workflow is a lightweight adaptation; it is not a port of that system.

## Existing software projects (OpenHands-inspired)

1. For a repository the user already has, use `project_workspace` to select/inspect it, read its `AGENTS.md`, and review Git status before proposing edits.
2. Make focused, reviewable changes through the staged project patch workflow. Show the diff and use its confirmation step before applying. Run project commands only through the confirmation-gated action.
3. Use `dev_agent` for a new standalone project; it creates a per-project Python virtual environment and has a bounded error-fix loop.
4. Do not say that edits run in a container: JARVIS's local project tools are not an OS-level sandbox. Prefer an explicitly isolated environment for untrusted code.

OpenHands can be connected as a separate service only after the user has deployed/configured its Agent Server and workspace. Do not send repository contents to a remote service unless the user has asked for that delegation.

## Specialist-agent patterns (Agent Zero-inspired)

- Use `parallel_subtasks` only for independent analysis. Those workers have no tools and cannot inspect files or make edits.
- Use `load_skill` for a matching installed skill; use project `AGENTS.md` through `project_workspace` for repository-specific instructions.
- Use the existing `workflow_manager` to save a multi-session plan when the user asks to track one. A saved workflow is not authorization to execute its future steps.
- Use configured MCP tools for connected external services. Check the server status and its confirmation policy; do not assume Agent Zero itself is connected.

## Reporting

State which native JARVIS actions were actually used, what evidence was checked, and what remains dependent on an external server, GPU, API key, or user confirmation. Never describe bounded tool use, memory, or error correction as model training or unlimited self-improvement.
