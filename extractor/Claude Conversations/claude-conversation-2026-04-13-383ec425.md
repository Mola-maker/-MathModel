# Claude Conversation Log

Session ID: 383ec425-3fd7-47b5-8d79-2cb351b4fb6b
Date: 2026-04-13 10:02:45

---

## 👤 User

<command-message>continuous-agent-loop</command-message>
<command-name>/continuous-agent-loop</command-name>

---

## 👤 User

Base directory for this skill: C:\Users\22494\.claude\skills\continuous-agent-loop

# Continuous Agent Loop

This is the v1.8+ canonical loop skill name. It supersedes `autonomous-loops` while keeping compatibility for one release.

## Loop Selection Flow

```text
Start
  |
  +-- Need strict CI/PR control? -- yes --> continuous-pr
  |
  +-- Need RFC decomposition? -- yes --> rfc-dag
  |
  +-- Need exploratory parallel generation? -- yes --> infinite
  |
  +-- default --> sequential
```

## Combined Pattern

Recommended production stack:
1. RFC decomposition (`ralphinho-rfc-pipeline`)
2. quality gates (`plankton-code-quality` + `/quality-gate`)
3. eval loop (`eval-harness`)
4. session persistence (`nanoclaw-repl`)

## Failure Modes

- loop churn without measurable progress
- repeated retries with same root cause
- merge queue stalls
- cost drift from unbounded escalation

## Recovery

- freeze loop
- run `/harness-audit`
- reduce scope to failing unit
- replay with explicit acceptance criteria


---

## 👤 User

[Request interrupted by user for tool use]

---

