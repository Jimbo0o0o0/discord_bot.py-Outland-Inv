import asyncio
import discord
from discord.ext import commands
from typing import Dict, Optional
import re


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


class DiscordConverter:
    """Utility class to resolve Discord role and channel inputs from mentions, IDs, or names."""

    @staticmethod
    async def resolve_role(bot: commands.Bot, role_input: str, guild: discord.Guild = None) -> discord.Role | None:
        """Resolve a role from a mention, ID, or name. Prefer the given guild, then search caches."""
        role_id = None
        role_name = None

        if role_input.startswith("<@&") and role_input.endswith(">"):
            role_id = role_input[3:-1]
        elif role_input.isdigit():
            role_id = role_input
        else:
            role_name = role_input.lower()

        if role_id:
            try:
                role_id_int = int(role_id)
            except ValueError:
                return None

            if guild:
                role = guild.get_role(role_id_int)
                if role:
                    return role
                try:
                    return await guild.fetch_role(role_id_int)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass

            for g in bot.guilds:
                role = g.get_role(role_id_int)
                if role:
                    return role
            return None

        if role_name:
            search_guilds = [guild] if guild else list(bot.guilds)
            for g in search_guilds:
                if g is None:
                    continue
                role = discord.utils.find(lambda r: r.name.lower() == role_name, g.roles)
                if role:
                    return role
            if guild:
                for g in bot.guilds:
                    role = discord.utils.find(lambda r: r.name.lower() == role_name, g.roles)
                    if role:
                        return role
        return None

    @staticmethod
    async def resolve_channel(bot: commands.Bot, channel_input: str, guild: discord.Guild = None) -> discord.TextChannel | None:
        """Resolve a text channel from a mention, ID, or name."""
        channel_id_str = None
        channel_name = None

        if channel_input.startswith("<#") and channel_input.endswith(">"):
            channel_id_str = channel_input[2:-1]
        elif channel_input.isdigit():
            channel_id_str = channel_input
        else:
            channel_name = channel_input.lower()

        if channel_id_str:
            try:
                channel_obj = await bot.fetch_channel(int(channel_id_str))
                if isinstance(channel_obj, discord.TextChannel):
                    return channel_obj
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                return None
            return None

        if channel_name:
            target_guild = guild or next((g for g in bot.guilds if g), None)
            if target_guild:
                channel = discord.utils.find(
                    lambda c: c.name.lower() == channel_name, target_guild.text_channels
                )
                if channel:
                    return channel
            for g in bot.guilds:
                channel = discord.utils.find(
                    lambda c: c.name.lower() == channel_name, g.text_channels
                )
                if channel:
                    return channel
        return None

    @staticmethod
    async def resolve_multiple_channels(bot: commands.Bot, inputs_str: str, guild: discord.Guild = None) -> list[discord.TextChannel]:
        """Parse space-separated channel inputs (names/mentions/IDs) into a list of TextChannels."""
        if not inputs_str.strip():
            return []

        parts = [part.strip() for part in re.split(r"\s+", inputs_str) if part.strip()]
        channels = []
        for part in parts:
            channel = await DiscordConverter.resolve_channel(bot, part, guild)
            if channel:
                channels.append(channel)
        return channels

    @staticmethod
    async def resolve_multiple_roles(bot: commands.Bot, inputs_str: str, guild: discord.Guild = None) -> list[discord.Role]:
        """Parse space-separated role inputs (names/mentions/IDs) into a list of Roles."""
        if not inputs_str.strip():
            return []

        parts = [part.strip() for part in re.split(r"\s+", inputs_str) if part.strip()]
        roles = []
        for part in parts:
            role = await DiscordConverter.resolve_role(bot, part, guild)
            if role:
                roles.append(role)
        return roles
