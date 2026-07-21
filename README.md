# Claude Code Py

Python 实现的 [Claude Code](https://github.com/anthropics/claude-code) — 终端中的 AI 编程助手，目标是 1:1 还原原版 TypeScript 版本的功能。

> **状态：Alpha。** 核心对话循环、工具、多后端、权限、会话等子系统均已实现并有测试覆盖，但仍在积极开发中，接口可能变动。

## 快速安装

一句话安装（二选一）：

```bash
# 方式 A：pip 直接从 GitHub 安装，装好 `aka` 命令（跨平台，推荐）
pip install "git+https://github.com/wstart/claude-code-py.git"

# 方式 B：一键脚本，装成自定义命令名并交互式配置 .env（默认命令名 aka）
curl -fsSL https://raw.githubusercontent.com/wstart/claude-code-py/main/scripts/install.sh | bash
# 指定命令名：
curl -fsSL https://raw.githubusercontent.com/wstart/claude-code-py/main/scripts/install.sh | bash -s -- mytool
```

方式 A 装完后命令是 **`aka`**，用环境变量配置 key 后即可运行；方式 B 会把仓库装到 `~/.aka` 并交互式写好 `~/.aka/.env`，之后直接用你指定的命令名启动。详细配置见下方 [配置](#配置)。

## 功能

- 🤖 **Agentic 对话循环** — 流式输出、自动调用工具、处理结果、多轮持续对话，含 Token 预算与最大轮次限制
- 📁 **文件工具** — Read / Write / Edit / MultiEdit / Glob / Grep / LS（自动编码探测、保留 CRLF、大文件保护）
- 💻 **Shell 执行** — 持久化 Bash 会话、超时控制、后台任务（BashOutput / KillShell）
- 🌐 **多后端** — Anthropic、OpenAI 兼容（Ollama / vLLM 等）、Amazon Bedrock、Google Vertex AI、Azure OpenAI
- 🔁 **健壮性** — 对可重试错误（429 / 5xx / 过载）做指数退避重试，且仅在流出内容前重试以避免重复输出
- 🔐 **权限系统** — allow / deny / ask 规则（deny 优先），manual / auto / plan / bypass 四种模式，macOS 沙箱
- 🪝 **Hooks** — PreToolUse / PostToolUse 等事件钩子；安全 gate 钩子执行失败时 fail-closed
- 🔌 **MCP** — Model Context Protocol 客户端（stdio / SSE 传输），发现并调用外部工具
- 🧩 **子代理与后台任务** — Task 工具派生只读研究子代理
- 🌐 **Web 工具** — WebFetch（含 SSRF 防护）/ WebSearch
- 🖥️ **终端 UI** — prompt_toolkit + rich（Markdown、代码高亮、Diff、状态栏）
- 💾 **会话管理** — 创建 / 恢复 / 持久化，完整保留 tool_use / tool_result 历史
- 📊 **成本追踪** — 按当前模型价格实时统计 Token 用量与费用估算
- 🔧 **斜杠命令** — 20+ 命令（见下）
- 📝 **CLAUDE.md** — 全局 + 项目级指令加载

## 安装

```bash
# 从源码开发安装
pip install -e ".[dev]"

# 可选后端依赖
pip install -e ".[aws]"    # Amazon Bedrock (boto3)
pip install -e ".[gcp]"    # Google Vertex AI
pip install -e ".[azure]"  # Azure OpenAI
pip install -e ".[mcp]"    # MCP 支持
pip install -e ".[all]"    # 全部可选依赖
```

也可用安装脚本以自定义命令名安装：

```bash
./scripts/install.sh            # 安装为默认命令名
./scripts/install.sh mytool     # 安装为 mytool
```

### 配置

通过环境变量（或 `~/.claude/.env`）配置：

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export ANTHROPIC_BASE_URL="https://api.anthropic.com"   # 可选，自定义/代理端点
export CLAUDE_MODEL="claude-sonnet-4-6"                  # 可选，默认模型
```

自定义 `base_url` 若是 **http 明文端点**可直接使用；若是**自签名 / 内网 HTTPS**证书导致连接失败，可显式关闭证书校验（不安全，默认开启校验）：

```bash
export CLAUDE_SKIP_SSL_VERIFY=1   # 跳过 TLS 证书验证，仅用于可信的内网/自签端点
```

## 使用

```bash
# 交互式模式
aka

# 带初始提问
aka "解释这个项目的架构"

# 非交互模式（print）
aka -p "写一个 Python hello world"

# 指定模型（支持别名，见 --model）
aka --model claude-sonnet-4-6 "优化这段代码"

# 恢复上次会话 / 指定会话
aka -c
aka -r <session-id>

# JSON / 流式 JSON 输出
aka -p --output-format json "列出所有 TODO"

# 权限模式：manual（默认）| auto | plan | bypass
aka --permission-mode auto "重构这个模块"
```

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清空对话 |
| `/compact` | 压缩上下文 |
| `/cost` | 显示 Token 用量与费用 |
| `/model [name]` | 查看 / 切换模型 |
| `/status` | 会话信息 |
| `/config` | 当前配置 |
| `/memory` | 显示 CLAUDE.md |
| `/init` | 生成项目 CLAUDE.md |
| `/permissions` | 查看 / 管理权限规则 |
| `/hooks` | 查看 hooks 配置 |
| `/mcp` | 管理 MCP 服务器 |
| `/agents` | 查看子代理 |
| `/plugins` | 管理插件 |
| `/add-dir` | 添加可访问目录 |
| `/review` | 代码审查 |
| `/rename` | 重命名会话 |
| `/doctor` | 诊断检查 |
| `/bug` | 反馈问题 |
| `/exit` | 退出 |

## 架构

```
src/claude_code/
├── cli.py                 # CLI 入口 (click)
├── core/                  # 核心引擎
│   ├── app.py             # 应用主类
│   ├── query_engine.py    # Agentic 对话循环 + 重试
│   ├── message.py         # 消息模型 (Pydantic)
│   ├── session.py         # 会话持久化
│   ├── context.py         # 上下文压缩
│   ├── config.py          # 配置加载
│   └── store.py           # 状态存储
├── providers/             # LLM 后端
│   ├── anthropic_provider.py
│   ├── openai_compat.py   # OpenAI 兼容 (Ollama/vLLM/…)
│   ├── bedrock.py         # Amazon Bedrock
│   ├── vertex.py          # Google Vertex AI
│   ├── azure.py           # Azure OpenAI
│   ├── streaming.py       # 流式事件归一化
│   └── retry.py           # 指数退避重试
├── tools/                 # 21 个工具 (文件/Shell/搜索/Web/编排)
├── ui/                    # 终端 UI (prompt_toolkit + rich)
├── permissions/           # 权限系统 (规则/模式/沙箱/提示)
├── hooks/                 # Hooks 系统
├── mcp/                   # MCP 协议客户端
├── agents/                # 子代理 + 后台任务
├── plugins/               # 插件系统
├── skills/                # 斜杠命令 + Skills
├── services/              # 认证 / 成本 / LSP
└── utils/                 # 工具函数
```

## 开发

```bash
# 运行测试
pytest tests/ -v

# Lint 与类型检查
ruff check src/ tests/
mypy src/claude_code
```

当前 141 个测试覆盖消息模型、上下文压缩、对话循环与重试、工具（文件/编码/搜索）、权限规则、provider 消息转换、会话恢复、hooks 等。

## 技术栈

- Python 3.11+
- [click](https://click.palletsprojects.com/) — CLI
- [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/) — 交互式输入
- [rich](https://rich.readthedocs.io/) — 终端渲染
- [pydantic](https://docs.pydantic.dev/) v2 — 数据校验
- [anthropic](https://github.com/anthropics/anthropic-sdk-python) / [openai](https://github.com/openai/openai-python) — LLM SDK
- [httpx](https://www.python-httpx.org/) — HTTP 客户端

## License

MIT
