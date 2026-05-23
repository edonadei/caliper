Output assets/caliper-demo.gif

Set Shell "zsh"
Set FontSize 18
Set Width 1040
Set Height 720
Set TypingSpeed 35ms

Type "clear"
Enter
Sleep 500ms

Type "cd skills/evaluate-skill/references/evals/screenshot"
Enter
Sleep 500ms

Type "echo 'Real screenshot eval: Python assert + Codex autorater'"
Enter
Sleep 1s

Type "caliper run screenshot.eval.yaml --k 1 --judge script --workers 1 --timeout 120 --verbose"
Enter
Sleep 24s

Type "clear"
Enter
Sleep 500ms

Type "caliper report screenshot"
Enter
Sleep 3s

Type "clear"
Enter
Sleep 500ms

Type "caliper install-skill codex --dry-run"
Enter
Sleep 2s

Type "clear"
Enter
Sleep 500ms

Type "caliper --help"
Enter
Sleep 2s

Sleep 2s
