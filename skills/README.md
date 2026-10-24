# JARVIS skills

Add reusable workflows as `skills/<skill-name>/SKILL.md`. Each file must begin
with YAML front matter containing a lowercase `name` matching its directory and
a one-line `description` that tells JARVIS when to use it:

```markdown
---
name: my-workflow
description: Use when the user asks to perform this particular workflow.
---

# Workflow instructions

Explain the steps, which JARVIS tools to call, and how to verify the result.
```

JARVIS adds only each skill's name and description to its regular prompt, then
loads the complete file on demand. Keep secrets out of skills. Instructions in
a skill do not grant new permissions or override what the user asked for.
