"""Entry point: python main.py"""

import os
import sys

# 确保 src 在 Python 路径中（必须早于 import claude_code）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from claude_code.utils.dotenv import load_dotenv_files  # noqa: E402

# 加载项目根目录的 .env（与 aka 命令一致）
load_dotenv_files(os.path.dirname(__file__))

from claude_code.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
