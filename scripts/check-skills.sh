#!/usr/bin/env bash
# Validates every skill in this repo.
# Each skill needs a SKILL.md with a name field that matches its directory, and a description field.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skills_dir="$root/dmitri-skills/skills"
status=0

for dir in "$skills_dir"/*/; do
  skill="$(basename "$dir")"
  file="$dir/SKILL.md"

  if [[ ! -f "$file" ]]; then
    echo "FAIL $skill: no SKILL.md"
    status=1
    continue
  fi

  frontmatter="$(awk 'NR==1 && $0!="---" {exit} NR>1 && $0=="---" {exit} NR>1' "$file")"

  if [[ -z "$frontmatter" ]]; then
    echo "FAIL $skill: no frontmatter"
    status=1
    continue
  fi

  name="$(printf '%s\n' "$frontmatter" | sed -n 's/^name:[[:space:]]*//p' | head -1)"
  description="$(printf '%s\n' "$frontmatter" | sed -n 's/^description:[[:space:]]*//p' | head -1)"

  if [[ "$name" != "$skill" ]]; then
    echo "FAIL $skill: name field is '$name'"
    status=1
    continue
  fi

  if [[ -z "$description" ]]; then
    echo "FAIL $skill: no description field"
    status=1
    continue
  fi

  echo "OK   $skill"
done

exit $status
