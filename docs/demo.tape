# Renders docs/demo.gif: `vhs docs/demo.tape` (https://github.com/charmbracelet/vhs).
# Real output from a live machine; see docs/demo-helpers.sh for the off-camera setup.
Output docs/demo.gif

Set Shell "bash"
Set FontSize 14
Set Width 1360
Set Height 420
Set Padding 18
Set TypingSpeed 22ms
Set Theme "Catppuccin Mocha"

Hide
Type "export PS1='$ '; source docs/demo-helpers.sh; clear" Enter
Show

Type "# One shared memory budget for every Claude Code and Codex session" Enter
Sleep 600ms
Type "memcap status" Enter
Sleep 5s

Type "clear" Enter
Type "# Searches and reads run immediately. Builds, tests and containers wait for memory." Enter
Sleep 600ms
Type `for c in 'rg TODO src' 'cat package.json' 'npm test' 'xcodebuild test' 'docker compose up -d'; do`
Enter
Type `  printf '%-22s %s\n' "$c" "$(memcap classify --command "$c" | jq -r .kind)"; done`
Enter
Sleep 5s
