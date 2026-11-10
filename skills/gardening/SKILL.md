---
name: gardening
description: Use when a task was harder than it should be, an agent produced bad code or tests, checks are slow, or the user asks to tend, weed, audit, or reduce friction in a codebase. Improves the codebase as a platform for agents - finds the cause behind bad output, removes weeds, cuts friction, and adds cheap checks that keep a property from regressing.
---

# Gardening

Leave the codebase easier for the next agent to work in.

## Audit

With no narrower request, measure before judging, then report findings ranked by payoff and change nothing.

- Time the full check command, and name its slowest steps.
- Compare test size with code size, and note how checks print.
- Measure the always-loaded agent instructions against each agent's load limit.
- List weeds and friction from the sections below, each with its evidence.

## When something went wrong

1. Find the source: the doc, example, test, or instruction that led there.
   If an agent wrote it, ask that thread why.
2. Fix the source, not only the instance. Delete misleading docs and bad examples.
3. If it can recur silently, add a stake.

## Weeds

Remove on sight:

- Tests that pin no behaviour a user or operator would miss.
- Docs and instructions that describe what is no longer true.
- Duplicate ways to do one thing, dead code, leftover one-off scripts.
- Lint or type suppressions that spread.

## Friction

Measure, then cut:

- Time the loop from edit to verified. Attack the slowest step first.
- Checks print only failures.
- Always-loaded instructions hold what every task needs; the rest sits behind a pointer that names when to read it.
- Anything that stops agents working in parallel is a defect.

## Stakes

For a property that keeps regressing, add one cheap check that prints the number on every change and fails on a meaningful regression.
Prefer it to a prose rule.

## Pendulum

When a feature fights back, stop, fix the platform, then return.
Platform changes are their own change sets, with no user-visible change.
