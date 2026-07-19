# Claude Code Py

Python 实现的 [Claude Code](https://github.com/anthropics/claude-code) — 终端中的 AI 编程助手。

## 功能

- 🤖 **Agentic 对话循环** — 自动调用工具、处理结果、持续对话
- 📁 **文件操作** — Read / Write / Edit / MultiEdit / Glob / Grep / LS
- 💻 **Shell 执行** — 持久化 Bash 会话
- 🌐 **多后端支持** — Anthropic / OpenAI 兼容（Ollama、vLLM 等）
- 🖥️ **终端 UI** — prompt_toolkit + rich 渲染（Markdown、代码高亮、Diff）
- 💾 **会话管理** — 创建 / 恢复 / 持久化会话
- 📊 **Token 追踪** — 实时统计用量和费用
- 🔧 **斜杠命令** — /help /clear /compact /cost /model /status /doctor
- 📝 **CLAUDE.md** — 全局 + 项目级指令加载

## 安装

```bash
# 开发模式
pip install -e ".[dev]"

# 设置 API Key
export ANTHROPIC_API_KEY="sk-ant-..."
```

## 使用

```bash
# 交互式模式
claude

# 带初始提问
claude "解释这个项目的架构"

# 非交互模式（print）
claude -p "写一个 Python hello world"

# 指定模型
claude --model claude-sonnet-4-20250514 "优化这段代码"

# 恢复上次会话
claude -c

# 恢复指定会话
claude -r <session-id>

# JSON 输出
claude -p --output-format json "列出所有 TODO"

# 跳过权限确认（危险）
claude --dangerously-skip-permissions "重构整个模块"
```

## 斜杠命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清空对话 |
| `/compact` | 压缩上下文 |
| `/cost` | 显示 Token 用量 |
| `/model [name]` | 查看/切换模型 |
| `/status` | 会话信息 |
| `/config` | 当前配置 |
| `/memory` | 显示 CLAUDE.md |
| `/doctor` | 诊断检查 |
| `/exit` | 退出 |

## 架构

```
src/claude_code/
├── cli.py              # CLI 入口 (click)
├── core/               # 核心引擎
│   ├── app.py          # 应用主类
│   ├── query_engine.py # Agentic 对话循环
│   ├── message.py      # 消息模型 (Pydantic)
│   ├── session.py      # 会话管理
│   ├── context.py      # 上下文压缩
│   ├── config.py       # 配置加载
│   └── store.py        # 状态存储
├── providers/          # LLM 后端
│   ├── anthropic_provider.py
│   ├── openai_compat.py
│   ├── streaming.py
│   └── retry.py
├── tools/              # 工具 (Read/Write/Edit/Bash/...)
├── ui/                 # 终端 UI (prompt_toolkit + rich)
├── permissions/        # 权限系统 (Phase 2)
├── hooks/              # Hooks 系统 (Phase 3)
├── mcp/                # MCP 协议 (Phase 3)
├── agents/             # 子代理 (Phase 5)
├── plugins/            # 插件 (Phase 6)
├── skills/             # 斜杠命令 (Phase 6)
└── utils/              # 工具函数
```

## 开发计划

- [x] **Phase 1**: 核心骨架 — CLI + 对话引擎 + 基础工具 + UI + 会话
- [ ] **Phase 2**: 权限系统 + 完整工具（Web/Notebook/Task）
- [ ] **Phase 3**: MCP 协议 + Hooks 系统
- [ ] **Phase 4**: 多后端（Bedrock/Vertex/Azure）+ OAuth
- [ ] **Phase 5**: 子代理 + 后台任务 + Worktree
- [ ] **Phase 6**: 插件 + Skills + 101 个斜杠命令
- [ ] **Phase 7**: 遥测 + IDE 集成 + 网关

## 技术栈

- Python 3.11+
- [click](https://click.palletsprojects.com/) — CLI
- [prompt_toolkit](https://python-prompt-toolkit.readthedocs.io/) — 交互式输入
- [rich](https://rich.readthedocs.io/) — 终端渲染
- [pydantic](https://docs.pydantic.dev/) v2 — 数据校验
- [anthropic](https://github.com/anthropics/anthropic-sdk-python) — LLM SDK
- [httpx](https://www.python-httpx.org/) — HTTP 客户端

## License

MIT
