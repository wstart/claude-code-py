"""Agent lifecycle manager — spawn, track, and cancel concurrent agents.

The :class:`AgentManager` owns the full lifecycle of sub-agents:
spawning them as asyncio tasks, waiting for results, and cleaning up
on cancellation or session exit.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from claude_code.agents.subagent import SubAgent

if TYPE_CHECKING:
    from claude_code.core.config import AppConfig
    from claude_code.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class AgentManager:
    """Manages multiple concurrent agents.

    Each agent runs as an asyncio task.  The manager tracks running
    agents, their statuses, and results.

    Usage::

        manager = AgentManager()
        agent_id = await manager.spawn(
            "research", "Find all usages of X",
            allowed_tools=["Read", "Grep", "Glob"],
            provider=provider, config=config,
        )
        result = await manager.wait(agent_id)
    """

    def __init__(self) -> None:
        self._agents: dict[str, SubAgent] = {}
        self._tasks: dict[str, asyncio.Task[str]] = {}
        self._results: dict[str, str] = {}
        self._statuses: dict[str, str] = {}
        self._start_times: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        name: str,
        prompt: str,
        allowed_tools: list[str] | None = None,
        provider: BaseProvider | None = None,
        config: AppConfig | None = None,
    ) -> str:
        """Spawn a new agent and return its unique ID.

        The agent starts running immediately as an asyncio task.

        Args:
            name: Human-readable name for the agent.
            prompt: The task prompt.
            allowed_tools: Tool names the agent may use (None = all).
            provider: LLM provider (required).
            config: Application config (required).

        Returns:
            A unique agent ID string.

        Raises:
            ValueError: If provider or config is None.
        """
        if provider is None:
            raise ValueError("provider is required to spawn an agent")
        if config is None:
            raise ValueError("config is required to spawn an agent")

        agent_id = uuid.uuid4().hex[:12]

        agent = SubAgent(
            provider=provider,
            config=config,
            allowed_tools=allowed_tools,
            description=name,
            name=f"{name}-{agent_id}",
        )

        async with self._lock:
            self._agents[agent_id] = agent
            self._statuses[agent_id] = "running"
            self._start_times[agent_id] = time.monotonic()

        task = asyncio.create_task(self._run_agent(agent_id, agent, prompt))
        self._tasks[agent_id] = task

        logger.info("Spawned agent '%s' (id=%s)", name, agent_id)
        return agent_id

    async def wait(self, agent_id: str, timeout: float = 300) -> str:
        """Wait for an agent to complete and return its result.

        Args:
            agent_id: The agent ID returned by :meth:`spawn`.
            timeout: Maximum seconds to wait (default 300).

        Returns:
            The agent's result text.

        Raises:
            KeyError: If agent_id is unknown.
            TimeoutError: If the timeout expires.
        """
        task = self._tasks.get(agent_id)
        if task is None:
            if agent_id in self._results:
                return self._results[agent_id]
            raise KeyError(f"Unknown agent: {agent_id}")

        try:
            result = await asyncio.wait_for(task, timeout=timeout)
            return result
        except TimeoutError:
            logger.warning("Agent '%s' timed out after %.0fs", agent_id, timeout)
            await self.cancel(agent_id)
            raise

    def list_agents(self) -> list[dict[str, Any]]:
        """Return info dicts for all known agents."""
        agents = []
        for agent_id, agent in self._agents.items():
            elapsed = time.monotonic() - self._start_times.get(agent_id, 0)
            agents.append({
                "id": agent_id,
                "name": agent.name,
                "description": agent.description,
                "status": self._statuses.get(agent_id, "unknown"),
                "elapsed_seconds": round(elapsed, 1),
            })
        return agents

    def get_status(self, agent_id: str) -> str:
        """Return the status string for an agent.

        Possible values: ``running``, ``completed``, ``failed``,
        ``cancelled``, ``unknown``.
        """
        return self._statuses.get(agent_id, "unknown")

    async def cancel(self, agent_id: str) -> None:
        """Cancel a running agent."""
        agent = self._agents.get(agent_id)
        if agent is not None:
            await agent.cancel()

        task = self._tasks.get(agent_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        self._statuses[agent_id] = "cancelled"
        logger.info("Cancelled agent '%s'", agent_id)

    async def cancel_all(self) -> None:
        """Cancel all running agents."""
        agent_ids = [
            aid for aid, status in self._statuses.items()
            if status == "running"
        ]
        for agent_id in agent_ids:
            await self.cancel(agent_id)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _run_agent(
        self, agent_id: str, agent: SubAgent, prompt: str
    ) -> str:
        """Run the agent task and store the result."""
        try:
            result = await agent.run(prompt)
            self._results[agent_id] = result
            self._statuses[agent_id] = "completed"
            return result
        except asyncio.CancelledError:
            self._statuses[agent_id] = "cancelled"
            self._results[agent_id] = "Cancelled"
            raise
        except Exception as exc:
            error_msg = f"Agent failed: {exc}"
            logger.exception("Agent '%s' failed", agent_id)
            self._results[agent_id] = error_msg
            self._statuses[agent_id] = "failed"
            return error_msg
