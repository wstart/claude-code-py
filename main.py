"""Entry point: python main.py"""

import sys
import os

# 确保 src 在 Python 路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def _load_dotenv() -> None:
    """Load .env file from project root into os.environ."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                # Don't override existing env vars
                if key and key not in os.environ:
                    os.environ[key] = value


_load_dotenv()

from claude_code.cli import main

if __name__ == "__main__":
    main()
