# Agent rules for this repo

This repo holds skills for both Claude Code and Codex.
Write every skill so both agents can use it.

- One skill per directory: `skills/<skill-name>/SKILL.md`.
  Use lowercase letters, digits, and hyphens in the name.
- The frontmatter has `name`, matching the directory, and `description`.
  Add other fields only when the user asks, and say which agent ignores them.
- Write the `description` as a trigger: when to use the skill, then what it does.
- Keep `SKILL.md` short.
  Move long reference material into `references/` and link to it from `SKILL.md`.
- Refer to support files by paths relative to the skill directory.
- Do not name a tool that only one agent has, such as `AskUserQuestion` or `apply_patch`.
  Describe the action instead, for example "ask the user".
- After a change, run `scripts/check-skills.sh` and then `scripts/install.sh`.
- Update the skill table in `README.md` when you add or remove a skill.
