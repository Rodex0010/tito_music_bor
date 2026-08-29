# ==============================================================================
# bot.py - Convenience Entrypoint
# ==============================================================================
# Lets you start the bot with:
#       python bot.py
# It behaves exactly like running:
#       python -m tito
# (same process, same working directory, same argv/exit code passed through).
# ==============================================================================

import subprocess
import sys

if __name__ == "__main__":
    result = subprocess.run(
        [sys.executable, "-m", "tito", *sys.argv[1:]]
    )
    sys.exit(result.returncode)
