import asyncio
import discord
from discord.ext import commands
from typing import Dict, Optional


class PresenceManager:
    """Global async presence manager for all cogs, supporting priority + guild-based activities."""

    def __init__(self, bot: commands.Bot, default_status: str):
        self.bot = bot
        self.default_status = default_status
        # module_name -> {text, priority, guild}
        self.activity_status: Dict[str, dict] = {}
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def set_activity(self, module_name: str, status_text: str, *,
                     priority: int = 0, activity_guild: str = "custom") -> None:
        """
        Register or update a module's activity.
        Higher priority overrides lower ones in the rotation order.
        """
        self.activity_status[module_name] = {
            "text": status_text,
            "priority": priority,
            "guild": activity_guild,
        }
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._cycle_presence())

    def clear_all(self) -> None:
        """Remove all activities from tracking and revert to default presence."""
        self.activity_status.clear()
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        self._task = asyncio.create_task(self.force_update())

    def clear_activity(self, module_name: str, activity_guild: Optional[str] = None) -> None:
        """Remove a module's activity from tracking."""
        if module_name in self.activity_status:
            if activity_guild is None or self.activity_status[module_name]["guild"] == activity_guild:
                self.activity_status.pop(module_name)

    def get_activity_by_guild(self, activity_guild: str) -> Optional[dict]:
        """Return the first active activity for a given guild (e.g., 'Bosscall')."""
        for info in self.activity_status.values():
            if info["guild"] == activity_guild:
                return info
        return None

    def get_highest_priority(self) -> Optional[dict]:
        """Return the single highest-priority activity (used for immediate display)."""
        if not self.activity_status:
            return None
        return max(self.activity_status.values(), key=lambda x: x["priority"])

    def has_activity(self, module_name: str) -> bool:
        """Check if a specific module currently has an active presence."""
        return module_name in self.activity_status

    def has_activity_guild(self, activity_guild: str) -> bool:
        """Check if a specific activity guild currently has an active presence."""
        return any(info["guild"] == activity_guild for info in self.activity_status.values())

    async def _cycle_presence(self) -> None:
        """Continuously update presence to show only the highest-priority activity."""
        try:
            while self.activity_status:
                async with self._lock:
                    top = self.get_highest_priority()
                    if top:
                        await self.bot.change_presence(
                            activity=discord.CustomActivity(name=top["text"]),
                            status=discord.Status.online
                        )

                await asyncio.sleep(30)  # Check for changes periodically

            # No active presence -> revert to default
            await self.bot.change_presence(
                activity=discord.CustomActivity(name=self.default_status),
                status=discord.Status.idle
            )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[PresenceManager] Error updating presence: {e}")

    async def force_update(self):
        """Immediately refresh to show the highest-priority activity."""
        async with self._lock:
            top = self.get_highest_priority()
            if top:
                await self.bot.change_presence(
                    activity=discord.CustomActivity(name=top["text"]),
                    status=discord.Status.online
                )
            else:
                await self.bot.change_presence(
                    activity=discord.CustomActivity(name=self.default_status),
                    status=discord.Status.idle
                )
