#!/usr/bin/env bash
# Links every skill in this repo into the skill directories of each agent.
#   ~/.agents/skills   read by Codex
#   ~/.claude/skills   read by Claude Code
# Links point back into this repo, so an edit here is live at once.
# Run it again after you add, rename, or delete a skill.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skills_dir="$root/skills"
targets=("$HOME/.agents/skills" "$HOME/.claude/skills")
status=0

for target in "${targets[@]}"; do
  mkdir -p "$target"

  # Remove links to skills that no longer exist in this repo.
  for link in "$target"/*; do
    [[ -L "$link" ]] || continue
    dest="$(readlink "$link")"
    if [[ "$dest" == "$skills_dir/"* && ! -e "$dest" ]]; then
      rm "$link"
      echo "UNLINK $link"
    fi
  done

  for dir in "$skills_dir"/*/; do
    [[ -d "$dir" ]] || continue
    skill="$(basename "$dir")"
    src="$skills_dir/$skill"
    link="$target/$skill"

    if [[ -L "$link" && "$(readlink "$link")" == "$src" ]]; then
      echo "OK     $link"
    elif [[ -e "$link" || -L "$link" ]]; then
      echo "SKIP   $link exists and does not point here"
      status=1
    else
      ln -s "$src" "$link"
      echo "LINK   $link"
    fi
  done
done

exit $status
