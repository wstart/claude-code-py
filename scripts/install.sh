#!/usr/bin/env bash
# AKA 安装脚本
# 用法: ./install.sh [命令名]
# 示例:
#   ./install.sh          # 默认安装为 aka
#   ./install.sh sentinel  # 安装为 sentinel
#   ./install.sh mytool    # 安装为 mytool

set -e

CMD_NAME="${1:-aka}"
INSTALL_DIR="$HOME/.aka"
REPO_URL="https://github.com/wstart/claude-code-py.git"

echo "▄▄▄▄▄  AKA Installer"
echo "█▀▀▀█  Command: $CMD_NAME"
echo "▀▀▀▀▀  Dir: $INSTALL_DIR"
echo ""

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "✗ Python3 not found. Install Python 3.11+ first."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓ Python $PY_VER"

# 克隆或更新
if [ -d "$INSTALL_DIR" ]; then
    echo "→ Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only 2>/dev/null || git fetch && git reset --hard origin/main
else
    echo "→ Cloning repository..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# 安装核心依赖（云后端按需: pip install ".[aws]" / ".[gcp]" / ".[azure]"）
echo "→ Installing dependencies..."
pip3 install -e . --quiet

# 创建命令链接
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

# 写入 wrapper 脚本
cat > "$BIN_DIR/$CMD_NAME" << 'WRAPPER'
#!/usr/bin/env bash
INSTALL_DIR="$HOME/.aka"
# 加载 .env
if [ -f "$INSTALL_DIR/.env" ]; then
    set -a
    source "$INSTALL_DIR/.env"
    set +a
fi
exec python3 "$INSTALL_DIR/main.py" "$@"
WRAPPER
chmod +x "$BIN_DIR/$CMD_NAME"

# 确保 ~/.local/bin 在 PATH 中
SHELL_RC=""
if [ -n "$ZSH_VERSION" ] || [ "$(basename "$SHELL")" = "zsh" ]; then
    SHELL_RC="$HOME/.zshrc"
elif [ -n "$BASH_VERSION" ] || [ "$(basename "$SHELL")" = "bash" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

if [ -n "$SHELL_RC" ] && ! grep -q '.local/bin' "$SHELL_RC" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    echo "→ Added ~/.local/bin to PATH in $SHELL_RC"
fi

# 配置 API
echo ""
if [ -f "$INSTALL_DIR/.env" ]; then
    echo "✓ .env already configured"
else
    echo "→ Configure API:"
    read -rp "  API Key: " API_KEY
    read -rp "  Base URL [https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic]: " BASE_URL
    read -rp "  Model [qwen3.7-max]: " MODEL
    BASE_URL="${BASE_URL:-https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic}"
    MODEL="${MODEL:-qwen3.7-max}"
    cat > "$INSTALL_DIR/.env" << EOF
ANTHROPIC_AUTH_TOKEN=$API_KEY
ANTHROPIC_BASE_URL=$BASE_URL
ANTHROPIC_MODEL=$MODEL
ANTHROPIC_DEFAULT_SONNET_MODEL=$MODEL
ANTHROPIC_DEFAULT_OPUS_MODEL=$MODEL
ANTHROPIC_DEFAULT_HAIKU_MODEL=$MODEL
CLAUDE_CODE_SUBAGENT_MODEL=$MODEL
EOF
    chmod 600 "$INSTALL_DIR/.env"
    echo "✓ .env saved"
fi

echo ""
echo "═══════════════════════════════════"
echo "  ✓ Installed as: $CMD_NAME"
echo ""
if [ -n "$SHELL_RC" ]; then
    echo "  Run: source $SHELL_RC"
    echo "  Then: $CMD_NAME"
else
    echo "  Run: export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "  Then: $CMD_NAME"
fi
echo "═══════════════════════════════════"
