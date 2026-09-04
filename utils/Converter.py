import discord
from discord.ext import commands
import re

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
