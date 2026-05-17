# Project: IoT OTA A/B Partition Simulator

## Core Tech Stack
- Backend: FastAPI (127.0.0.1)
- CLI Client: Click + Requests
- Terminal UI: Rich

## Active Skills (MUST FOLLOW)

### 1. [cli-visual-master]
NEVER use standard `print()`. You MUST use `rich.console` for colored outputs, `rich.progress` for file downloads/generation, and `rich.table` to display the current A/B slot status clearly. The terminal output must look professional and beautiful for a final video demonstration.

### 2. [ota-defensive-coder]
Assume network drops or file corruption. Explicitly log state transitions (Current Active Slot, Target Slot, Hash Verification Result). NEVER switch the active partition flag unless the dummy file is 100% verified. Implement strict fallback logic.

### 3. [prompt-log-tracker]
Every time we complete a major step or fix a bug, you MUST automatically append a brief, professional summary to `docs/AI_Prompt_Log.md`. Format:
- **User Prompt / Intent:** [What the user asked]
- **AI Solution:** [Brief technical explanation]

### 4. [superpowers]
You possess advanced software engineering superpowers. ALWAYS follow these principles:
- Think step-by-step before writing code.
- Write defensive, clean, and self-documenting code.
- Anticipate edge cases (e.g., file locks, missing directories) and handle them gracefully.
- If a command fails during execution, auto-analyze the error and fix it without waiting for my prompt.