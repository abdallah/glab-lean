#!/usr/bin/env sh
# Install glab-lean: the CLI into a bin directory and the agent skill into each agent's skills directory.
#
#   ./install.sh                 # ~/.local/bin, ~/.claude/skills and ~/.agents/skills
#   ./install.sh --copy          # copy files instead of symlinking to this checkout
#   BIN_DIR=/usr/local/bin SKILLS_DIRS="$HOME/.claude/skills" ./install.sh
#
# ~/.claude/skills is read by Claude Code (and OpenCode). ~/.agents/skills is the shared
# location read by Codex, Gemini CLI, and OpenCode.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
bin_dir=${BIN_DIR:-"$HOME/.local/bin"}
skills_dirs=${SKILLS_DIRS:-"$HOME/.claude/skills $HOME/.agents/skills"}
mode=link
[ "${1:-}" = "--copy" ] && mode=copy

command -v python3 >/dev/null || { echo "glab-lean needs python3" >&2; exit 1; }
command -v glab >/dev/null || echo "warning: glab not found on PATH; install it and run 'glab auth login'" >&2

place() {  # place SRC DEST
  mkdir -p "$(dirname "$2")"
  if { [ -e "$2" ] || [ -L "$2" ]; } && [ "$(readlink "$2" 2>/dev/null)" != "$1" ]; then
    echo "  replacing existing $2" >&2
  fi
  if [ "$mode" = copy ]; then
    rm -f "$2"
    cp "$1" "$2"
  else
    ln -sfn "$1" "$2"
  fi
  echo "  $2"
}

chmod +x "$here/bin/glab-lean"
echo "installed ($mode):"
place "$here/bin/glab-lean" "$bin_dir/glab-lean"
for dir in $skills_dirs; do
  place "$here/skills/glab-lean/SKILL.md" "$dir/glab-lean/SKILL.md"
done

case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) echo "note: $bin_dir is not on PATH" >&2 ;;
esac
