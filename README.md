# dmitri-skills

Personal Claude Code skills, packaged as a plugin marketplace.

## Install

Add the marketplace, then install the plugin.

```bash
claude
/plugin marketplace add chillyweather/skills
/plugin install dmitri-skills@dmitri-skills-marketplace
```

Use a local path instead of the GitHub name when you work on the repo directly.

```bash
/plugin marketplace add ~/personal/workspace/skills
```

## Skills

| Skill | Purpose |
| --- | --- |
| `issue-map` | Refresh `docs/open-issue-map.md` from GitHub. Facts come from `gh`, judgment stays in the document. |
| `explain-diff-html` | Make a rich HTML explanation of a code change, diff, branch, or pull request. |

## Layout

```
.claude-plugin/marketplace.json   The marketplace manifest. One entry for each plugin.
dmitri-skills/
  .claude-plugin/plugin.json      The plugin manifest. Name, version, and description.
  skills/
    <skill-name>/SKILL.md         One directory for each skill.
scripts/check-skills.sh           Validates the frontmatter of every skill.
```

## Add a skill

1. Make the directory `dmitri-skills/skills/<skill-name>/`.
2. Write `SKILL.md` in that directory.
   The frontmatter needs a `name` field that matches the directory name, and a `description` field.
   Add `disable-model-invocation: true` when the skill must run only from `/<skill-name>`.
3. Put support files next to `SKILL.md`, for example `reference.md` or a `template/` directory.
4. Run `scripts/check-skills.sh`.
5. Raise the `version` field in `dmitri-skills/.claude-plugin/plugin.json`.
6. Commit and push.

Claude Code reads the installed copy of a plugin.
Run `/plugin marketplace update dmitri-skills-marketplace` to get your new commits.

## Skill frontmatter

```yaml
---
name: skill-name
description: When to use this skill, and what it does.
disable-model-invocation: true   # optional. Blocks automatic use.
---
```

The `description` field is the only text Claude reads before it decides to use a skill.
Write it as a trigger, not as a title.
