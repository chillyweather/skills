---
name: ui-climb
description: Use when the user wants an agent to improve how a web UI or generated HTML page looks and reads through iterative self-review, asks to "hill climb" or polish a UI with model feedback, or wants a second model to judge design changes. Runs a budget-capped loop - this agent edits, free automated checks gate each version, and the other model (Codex or Claude) blindly compares the candidate against the current best from screenshots - and ends with the user picking between the old and the new version.
---

# UI climb

Improve a page's design in rounds.
This agent builds.
The other model judges, blind, from screenshots.
Free checks run first, so a paid judge call is spent only on versions that pass them.

The helper `scripts/climb.py` sits next to this file.
Run it as `python3 <this skill's directory>/scripts/climb.py <command>`.
It uses only the Python standard library and a Chromium-family browser (set `UI_CLIMB_BROWSER` if it is not found).
Run `climb.py <command> --help` for every option.

## Rules

- The judge is the other agent: `--judge codex` when you are Claude, `--judge claude` when you are Codex.
  Never judge your own work.
- Keep the judge blind.
  Never tell it which version is new, how many rounds have run, or what score or outcome you hope for.
  The script shuffles A and B and maps the answer back.
- Change presentation only.
  The content, data, and behaviour of the page stay the same, and the project's own tests must still pass.
- Render every version from the same frozen input, so screenshots differ only by design.
- Respect the budget the user sets.
  Default: at most 5 judged rounds.
- The user's choice at the end overrides every judge verdict.

## Step 1 - set up

Agree on these with the user before the first round.
Ask only about what you cannot find in the project.

1. **The page and how to build it.**
   One command that renders a local HTML file from frozen input, for example a fixed data snapshot or test fixture.
   If the page is served, save it to a file first.
2. **Viewports.**
   Default `desktop=1280x1800,phone=390x1600`.
   The height sets how much of the page the judge sees, so cover the part that matters most.
3. **The rubric.**
   Write `.ui-climb/rubric.md` in the project from `references/rubric-template.md`.
   It names the real users, the tasks they do on this page, and 4 to 7 concrete criteria.
   Show it to the user, because it decides what "better" means.
4. **Budget.**
   The maximum number of judged rounds, and whether to use `--both-orders`, which halves position bias and doubles cost.
   Use `--effort low` or `medium` for the judge on small plans.

Keep all working files under `.ui-climb/` in the project, and add that directory to `.gitignore`.
Work on a new branch.

## Step 2 - baseline

1. Render the page and run `climb.py shoot <page.html> --out .ui-climb/r00`.
2. Run `climb.py check .ui-climb/r00` without a baseline to list the existing problems.
3. Look at the screenshots yourself.
   Note what fails the rubric.

`r00` is the first "best".

## Step 3 - one round

1. Pick the 1 to 3 changes with the largest expected effect.
   Use the free-check failures, the last judge's `fixes_for_loser` or `fixes_for_winner`, and your own read of the screenshots.
   Make each round a coherent idea, not a scatter of tweaks.
2. Edit the code, rebuild the page, and run the project's tests.
3. Run `climb.py shoot <page.html> --out .ui-climb/rNN`.
4. Run `climb.py check .ui-climb/rNN --baseline <best>`.
   On failure, fix and shoot again.
   This step is free, so repeat it as often as needed.
5. Look at the new screenshots yourself before paying for a judgment.
6. Run `climb.py judge --a <best> --b .ui-climb/rNN --rubric .ui-climb/rubric.md --judge <other> --ledger .ui-climb/rounds.jsonl`.
7. If the candidate wins, commit it, and it becomes the new best.
   If it loses or ties, revert the code to the best version and keep the judge's notes for the next idea.

## Step 4 - stop

Stop when any of these is true:

- the round budget is spent
- two judged rounds in a row did not produce a win
- the judge's `fixes_for_winner` are only cosmetic nitpicks

## Step 5 - hand over

Show the user the `r00` and final screenshots side by side, with one line for each kept change and the judge's reason for it.
Ask which version they prefer.
Read `.ui-climb/rounds.jsonl` for the history.
Do not merge or publish until the user chooses.
