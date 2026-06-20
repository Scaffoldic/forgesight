# ForgeSight README demo — install → instrument → run. Regenerate: vhs docs/assets/demo.tape
# (requires the prepared venv at /tmp/fs-gif; see the prep steps in the PR.)
Output docs/assets/demo.gif

Set Shell zsh
Set FontSize 20
Set Width 1320
Set Height 640
Set Padding 22
Set Theme "Dracula"
Set TypingSpeed 45ms

Hide
Type "cd /tmp/fs-gif && export VIRTUAL_ENV_DISABLE_PROMPT=1 PIP_DISABLE_PIP_VERSION_CHECK=1 && source venv/bin/activate && export PROMPT='$ ' && clear"
Enter
Sleep 1500ms
Show

# 1) install
Type "pip install -q 'forgesight[otel]'"
Sleep 400ms
Enter
Sleep 2500ms
Type "pip list | grep forgesight"
Sleep 400ms
Enter
Sleep 3000ms
Type "clear"
Enter
Sleep 300ms

# 2) instrument any agent — wrap it, that's it
Type "cat quickstart.py"
Sleep 400ms
Enter
Sleep 5500ms
Type "clear"
Enter
Sleep 300ms

# 3) run it
Type "python quickstart.py"
Sleep 400ms
Enter
Sleep 4500ms
