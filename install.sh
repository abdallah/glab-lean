#!/usr/bin/env sh
# Install glab-lean: the CLI into a bin directory and the agent skill into Claude Code's skills.
#
#   ./install.sh                 # ~/.local/bin and ~/.claude/skills
#   BIN_DIR=/usr/local/bin SKILLS_DIR=/path/to/skills ./install.sh
#   ./install.sh --copy          # copy files instead of symlinking to this checkout
set -eu

here=$(cd "$(dirname "$0")" && pwd)
bin_dir=${BIN_DIR:-"$HOME/.local/bin"}
skills_dir=${SKILLS_DIR:-"$HOME/.claude/skills"}
mode=link
[ "${1:-}" = "--copy" ] && mode=copy

command -v python3 >/dev/null || { echo "glab-lean needs python3" >&2; exit 1; }
command -v glab >/dev/null || echo "warning: glab not found on PATH; install it and run 'glab auth login'" >&2

mkdir -p "$bin_dir" "$skills_dir/glab-lean"
chmod +x "$here/bin/glab-lean"
if [ "$mode" = copy ]; then
  cp "$here/bin/glab-lean" "$bin_dir/glab-lean"
  cp "$here/skills/glab-lean/SKILL.md" "$skills_dir/glab-lean/SKILL.md"
else
  ln -sf "$here/bin/glab-lean" "$bin_dir/glab-lean"
  ln -sf "$here/skills/glab-lean/SKILL.md" "$skills_dir/glab-lean/SKILL.md"
fi

echo "installed ($mode): $bin_dir/glab-lean, $skills_dir/glab-lean/SKILL.md"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "note: $bin_dir is not on PATH" >&2 ;;
esac
