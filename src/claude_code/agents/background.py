"""Background agent — long-running daemon-style agent execution.

A :class:`BackgroundAgent` runs an agent task detached from the
interactive session, similar to a daemon process.  The
:class:`DaemonManager` provides lifecycle management for multiple
background agents.
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


class BackgroundAgent:
    """Agent that runs in the background, similar to a daemon.

    Unlike a regular :class:`SubAgent` which is awaited inline, a
    ``BackgroundAgent`` manages its own asyncio task and exposes
    methods to start, stop, and retrieve output.

    Parameters
    ----------
    agent_id:
        Unique identifier for this background agent.
    prompt:
        The task prompt.
    provider:
        LLM provider for inference.
    config:
        Application configuration.
    allowed_tools:
        Optional tool restriction list.
    """

    def __init__(
        self,
        agent_id: str,
        prompt: str,
        provider: BaseProvider,
        config: AppConfig,
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._prompt = prompt
        self._provider = provider
        self._config = config
        self._allowed_tools = allowed_tools

        self._task: asyncio.Task[str] | None = None
        self._output: str = ""
        self._output_lines: list[str] = []
        self._running = False
        self._start_time: float = 0
        self._end_time: float = 0

        self._agent = SubAgent(
            provider=provider,
            config=config,
            allowed_tools=allowed_tools,
            description=f"background-{agent_id}",
            name=f"bg-{agent_id}",
        )

    async def start(self) -> None:
        """Start running the agent in the background."""
        if self._running:
            return

        self._running = True
        self._start_time = time.monotonic()
        self._task = asyncio.create_task(self._run())
        logger.info("Background agent '%s' started", self.agent_id)

    async def stop(self) -> None:
        """Stop the background agent."""
        if not self._running:
            return

        await self._agent.cancel()

        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

        self._running = False
        self._end_time = time.monotonic()
        logger.info("Background agent '%s' stopped", self.agent_id)

    async def get_output(self) -> str:
        """Return the accumulated output from the agent.

        If the agent is still running, returns whatever output has been
        collected so far.  If completed, returns the full result.
        """
        if self._task is not None and self._task.done():
            try:
                self._output = self._task.result()
            except (asyncio.CancelledError, Exception) as exc:
                self._output = f"Agent ended with error: {exc}"

        return self._output

    def is_running(self) -> bool:
        """Return whether the agent is currently running."""
        if self._task is None:
            return False
        return self._running and not self._task.done()

    def get_info(self) -> dict[str, Any]:
        """Return status info for this agent."""
        elapsed = 0.0
        if self._start_time > 0:
            end = self._end_time if self._end_time > 0 else time.monotonic()
            elapsed = end - self._start_time

        status = "running" if self.is_running() else (
            "completed" if self._task and self._task.done() else "stopped"
        )

        return {
            "id": self.agent_id,
            "status": status,
            "elapsed_seconds": round(elapsed, 1),
            "output_length": len(self._output),
        }

    async def _run(self) -> str:
        """Internal task entry point."""
        try:
            result = await self._agent.run(self._prompt)
            self._output = result
            return result
        except asyncio.CancelledError:
            self._output = "Cancelled"
            raise
        except Exception as exc:
            self._output = f"Error: {exc}"
            logger.exception("Background agent '%s' failed", self.agent_id)
            return self._output
        finally:
            self._running = False
            self._end_time = time.monotonic()


class DaemonManager:
    """Manages background agent processes.

    Provides launch, attach, stop, and log retrieval for long-running
    agents that operate independently of the interactive session.

    Usage::

        daemon = DaemonManager(provider=provider, config=config)
        agent_id = await daemon.launch("Analyse the codebase...")
        # ... later ...
        logs = await daemon.get_logs(agent_id)
        await daemon.stop(agent_id)
    """

    def __init__(
        self,
        provider: BaseProvider | None = None,
        config: AppConfig | None = None,
    ) -> None:
        self._provider = provider
        self._config = config
        self._agents: dict[str, BackgroundAgent] = {}

    async def launch(
        self,
        prompt: str,
        allowed_tools: list[str] | None = None,
    ) -> str:
        """Launch a background agent and return its ID.

        Args:
            prompt: The task prompt for the agent.
            allowed_tools: Optional tool restriction list.

        Returns:
            Unique agent ID string.

        Raises:
            ValueError: If provider or config has not been set.
        """
        if self._provider is None:
            raise ValueError("DaemonManager requires a provider")
        if self._config is None:
            raise ValueError("DaemonManager requires a config")

        agent_id = uuid.uuid4().hex[:12]

        agent = BackgroundAgent(
            agent_id=agent_id,
            prompt=prompt,
            provider=self._provider,
            config=self._config,
            allowed_tools=allowed_tools,
        )

        self._agents[agent_id] = agent
        await agent.start()

        logger.info("Launched background agent '%s'", agent_id)
        return agent_id

    async def attach(self, agent_id: str) -> None:
        """Attach to a running agent's output, waiting for completion.

        Blocks until the agent finishes or is stopped.

        Args:
            agent_id: The agent to attach to.

        Raises:
            KeyError: If agent_id is unknown.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Unknown background agent: {agent_id}")

        if agent._task is not None:
            try:
                await agent._task
            except (asyncio.CancelledError, Exception):
                pass

    async def list_running(self) -> list[dict[str, Any]]:
        """Return info for all background agents."""
        return [agent.get_info() for agent in self._agents.values()]

    async def stop(self, agent_id: str) -> None:
        """Stop a specific background agent.

        Args:
            agent_id: The agent to stop.

        Raises:
            KeyError: If agent_id is unknown.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Unknown background agent: {agent_id}")

        await agent.stop()
        logger.info("Stopped background agent '%s'", agent_id)

    async def stop_all(self) -> None:
        """Stop all running background agents."""
        for agent in self._agents.values():
            if agent.is_running():
                await agent.stop()

    async def get_logs(self, agent_id: str) -> str:
        """Retrieve the output logs for a background agent.

        Args:
            agent_id: The agent to get logs for.

        Returns:
            The agent's accumulated output text.

        Raises:
            KeyError: If agent_id is unknown.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(f"Unknown background agent: {agent_id}")

        return await agent.get_output()
