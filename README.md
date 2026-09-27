# skills

Personal agent skills, shared by Claude Code and Codex.

Both agents read the same format: a directory with a `SKILL.md` file.
This repo holds one copy of each skill and links it into both agents.

## Skills

| Skill | Purpose |
| --- | --- |
| `jellyfin-organize` | Rename and arrange a downloaded anime, series, or movie folder for Jellyfin. Adds TMDB `.nfo` files, artwork, and missing subtitles. |
| `navidrome-organize` | Split CUE images, then tag and arrange a downloaded album or discography for Navidrome from MusicBrainz. Picks a square cover, preferring the LP edition, and adds artist images. |

## Install

```bash
git clone git@github.com:chillyweather/skills.git ~/workspace/skills
~/workspace/skills/scripts/install.sh
```

The script links each skill into two places:

| Directory | Agent |
| --- | --- |
| `~/.agents/skills/<name>` | Codex |
| `~/.claude/skills/<name>` | Claude Code |

The links point back into this repo, so an edit is live at once.
Run the script again after you add, rename, or delete a skill.
It never overwrites a skill that it did not link.

## Layout

```
skills/
  <skill-name>/
    SKILL.md              Required. Frontmatter and instructions.
    references/           Optional. Docs the agent reads when needed.
    scripts/              Optional. Code the agent runs.
    assets/               Optional. Templates and other output files.
scripts/
  install.sh              Links every skill into both agents.
  check-skills.sh         Validates the frontmatter of every skill.
AGENTS.md                 Rules for agents that write skills here.
```

## Add a skill

1. Make the directory `skills/<skill-name>/`.
2. Write `SKILL.md` in that directory.
3. Run `scripts/check-skills.sh`.
4. Run `scripts/install.sh`.
5. Commit and push.

## Skill frontmatter

```yaml
---
name: skill-name
description: When to use this skill, and what it does.
---
```

`name` must match the directory name.
`description` is the only text an agent reads before it decides to use a skill.
Write it as a trigger, not as a title.

Keep to these two fields so the skill works in both agents.
Claude-only fields, such as `disable-model-invocation`, are ignored by Codex.
