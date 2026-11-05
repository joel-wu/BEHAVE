Run Bash commands in the task sandbox. Put the command or multiline Bash script in `command`, for example `{"command": "ls"}`. Pipes and redirections work as in Bash. Each call starts a fresh shell in `/workspace`. Files in `/workspace` and `/tmp` persist across calls within this attempt. Each command has a wall-time limit of ${wall_seconds} seconds. Returns stdout, stderr, exit status and elapsed wall time, including sandbox startup and cleanup.

${output_preview}
