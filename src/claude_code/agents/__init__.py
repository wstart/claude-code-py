"""Agent subsystem — sub-agents, lifecycle manager, and background agents.

Public API
----------
- :class:`SubAgent` — isolated agent with restricted tool set.
- :class:`AgentManager` — spawn, track, and cancel concurrent agents.
- :class:`BackgroundAgent` — long-running daemon-style agent.
- :class:`DaemonManager` — manage background agent processes.
"""

from claude_code.agents.background import BackgroundAgent, DaemonManager
from claude_code.agents.manager import AgentManager
from claude_code.agents.subagent import SubAgent

__all__ = [
    "AgentManager",
    "BackgroundAgent",
    "DaemonManager",
    "SubAgent",
]
