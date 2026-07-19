# Claude Code Py — 项目约定

## 概述
Claude Code 的 Python 实现，目标是 1:1 还原原版 TypeScript 版本的全部功能。

## 技术栈
- Python 3.11+
- CLI: click
- UI: prompt_toolkit + rich
- LLM: anthropic SDK (主), openai SDK (兼容层)
- 异步: asyncio + httpx
- 校验: pydantic v2

## 代码规范
- 遵循 PEP 8，使用类型注解
- 行宽 100 字符
- 使用 ruff 做 lint，mypy 做类型检查
- 测试使用 pytest + pytest-asyncio

## 目录约定
- `src/claude_code/` — 源码根目录
- `src/claude_code/core/` — 核心引擎（对话循环、消息、配置）
- `src/claude_code/tools/` — 工具实现
- `src/claude_code/providers/` — LLM 后端适配
- `src/claude_code/ui/` — 终端 UI
- `src/claude_code/permissions/` — 权限系统
- `src/claude_code/mcp/` — MCP 协议
- `tests/` — 测试文件

## 测试
```bash
pytest tests/ -v
```

## 运行
```bash
# 开发模式
pip install -e ".[dev]"

# 运行
claude "hello"
python -m claude_code "hello"
```
