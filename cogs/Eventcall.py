import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from utils.Converter import DiscordConverter

#
# Custom event calls — same ping / cancel / timer / presence flow as BossCall,
# but created with a command instead of a reaction menu.
#

ACTIVITY_KEY = "Eventcall"
DEFAULT_MINUTES = 15
MIN_MINUTES = 1
MAX_MINUTES = 1440  # 24 hours
MAX_NAME_LENGTH = 80
cancel_emojis = ["❌", "🔕"]

# "Faction VIP in 20", "Dungeon in 15m", "Treasure Map in 2 hours"
_TRAILING_IN = re.compile(
    r"^(?P<name>.+?)\s+in\s+(?P<num>\d+)\s*(?P<unit>m|min|mins|minutes|h|hr|hrs|hour|hours)?\s*$",
    re.IGNORECASE,
)
# "20 Faction VIP", "15m Dungeon", "2h Treasure Map"
_LEADING_DURATION = re.compile(
    r"^(?P<num>\d+)\s*(?P<unit>m|min|mins|minutes|h|hr|hrs|hour|hours)?\s+(?P<name>.+)$",
    re.IGNORECASE,
)

Source = Union[commands.Context, discord.Interaction]


def _minutes_from_match(match: re.Match) -> int:
    num = int(match.group("num"))
    unit = (match.group("unit") or "m").lower()
    if unit.startswith("h"):
        return num * 60
    return num


def parse_event_call_args(details: str, default_minutes: int = DEFAULT_MINUTES) -> tuple[str, int]:
    """Parse `!eventcall` text into (name, minutes).

    Accepted forms:
      Faction VIP
      Faction VIP in 20
      Dungeon in 15m
      20 Faction VIP
      2h Treasure Map
    """
    details = " ".join((details or "").split())
    if not details:
        raise ValueError("Event name is required.")

    for pattern in (_TRAILING_IN, _LEADING_DURATION):
        match = pattern.match(details)
        if match:
            name = match.group("name").strip(" \"'")
            if name:
                return name, _minutes_from_match(match)

    return details.strip(" \"'"), default_minutes


def sanitize_event_name(name: str) -> str:
    name = discord.utils.escape_mentions(" ".join((name or "").split()))
    return name[:MAX_NAME_LENGTH]


class EventCall(commands.Cog):
    """Custom event calls via command — pings, cancel menu, timer, presence."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.presence_manager = bot.presence_manager
        self._tasks: Dict[str, asyncio.Task] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    # ---------------- Helpers ----------------

    def _guild_lock(self, guild_id: str) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def _get_guild_logger(self, guild: discord.Guild):
        logger = getattr(self.bot, "logger", None)
        if logger and hasattr(logger, "get_guild_logger"):
            try:
                return logger.get_guild_logger(guild, self.bot.loop)
            except Exception:
                return logger.base_logger
        if logger and hasattr(logger, "base_logger"):
            return logger.base_logger
        return logging.getLogger("EventCall")

    async def _safe_remove_reaction(self, reaction: discord.Reaction, user: discord.abc.Snowflake) -> bool:
        channel = reaction.message.channel
        guild = reaction.message.guild
        try:
            bot_member = guild.get_member(self.bot.user.id) if guild else None
            if bot_member and channel.permissions_for(bot_member).manage_messages:
                try:
                    await reaction.remove(user)
                    return True
                except Exception:
                    try:
                        await reaction.message.remove_reaction(reaction.emoji, user)
                        return True
                    except Exception:
                        return False
            return False
        except Exception:
            try:
                await reaction.remove(user)
                return True
            except Exception:
                return False

    async def _safe_delete_message(self, channel: discord.TextChannel, message_id: int) -> bool:
        if channel is None:
            return False
        try:
            msg = await channel.fetch_message(message_id)
            try:
                await msg.delete()
                return True
            except (discord.Forbidden, discord.NotFound):
                return False
        except discord.NotFound:
            return True
        except Exception:
            return False

    def _format_delay(self, seconds: int) -> str:
        minutes = max(1, seconds // 60)
        if minutes == 1:
            return "1 minute"
        if minutes < 60:
            return f"{minutes} minutes"
        hours = minutes // 60
        rem = minutes % 60
        if rem == 0:
            return f"{hours} hour{'s' if hours != 1 else ''}"
        return f"{hours}h {rem}m"

    async def _respond(
        self,
        source: Source,
        content: str,
        *,
        ephemeral: bool = False,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        kwargs: Dict[str, Any] = {}
        if content:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if not kwargs:
            kwargs["content"] = "\u200b"
        if isinstance(source, discord.Interaction):
            if source.response.is_done():
                await source.followup.send(ephemeral=ephemeral, **kwargs)
            else:
                await source.response.send_message(ephemeral=ephemeral, **kwargs)
        else:
            await source.send(**kwargs)

    def _is_admin(self, user: discord.abc.User, guild: discord.Guild) -> bool:
        if not isinstance(user, discord.Member):
            member = guild.get_member(user.id)
        else:
            member = user
        if member is None:
            return False
        return bool(member.guild_permissions.administrator)

    # ---------------- Settings ----------------

    async def _load_settings(self, guild_id: str) -> Dict[str, Any]:
        """Eventcall settings, falling back to Bosscall channels/roles if unset."""
        event = await self.db.get("eventcall", guild_id, {}) or {}
        if not isinstance(event, dict):
            event = {}
        boss = await self.db.get("bosscall", guild_id, {}) or {}
        if not isinstance(boss, dict):
            boss = {}

        return {
            "command_channel_ids": event.get("command_channel_ids") or boss.get("command_channel_ids") or [],
            "call_channel_ids": event.get("call_channel_ids") or boss.get("call_channel_ids") or [],
            "allowed_roles": event.get("allowed_roles") or boss.get("allowed_roles") or [],
            "cancel_message_ids": event.get("cancel_message_ids") or {},
            "active": event.get("active"),
            "_event": event,
        }

    async def _save_event_state(self, guild_id: str, settings: Dict[str, Any]) -> None:
        event = settings.get("_event")
        if not isinstance(event, dict):
            event = await self.db.get("eventcall", guild_id, {}) or {}
            if not isinstance(event, dict):
                event = {}
        event["cancel_message_ids"] = settings.get("cancel_message_ids") or {}
        if settings.get("active"):
            event["active"] = settings["active"]
        else:
            event.pop("active", None)
        await self.db.set("eventcall", guild_id, event, save=True)
        settings["_event"] = event

    # ---------------- Permissions / conflicts ----------------

    async def _check_user_permission(
        self,
        user: discord.User | discord.Member,
        guild: discord.Guild,
        settings: Dict[str, Any],
        *,
        dm_on_fail: bool = False,
    ) -> bool:
        if self._is_admin(user, guild):
            return True

        allowed_roles = set(settings.get("allowed_roles", []))
        if not allowed_roles:
            return False

        if not hasattr(user, "roles"):
            if dm_on_fail:
                try:
                    await user.send("You do not have permission to call an activity.")
                except Exception:
                    pass
            self._get_guild_logger(guild).warning(
                f"Non-member tried custom event call: {getattr(user, 'id', None)}"
            )
            return False

        user_roles = {r.id for r in user.roles}
        if not (user_roles & allowed_roles):
            if dm_on_fail:
                try:
                    await user.send("You do not have permission to call an activity.")
                except Exception:
                    pass
            self._get_guild_logger(guild).warning(f"Unauthorized custom event call attempt by {user}")
            return False

        return True

    def _presence_key(self, guild_id: str) -> str:
        """Per-guild presence slot so EventCall never collides with Bosscall or other servers."""
        return f"{ACTIVITY_KEY}:{guild_id}"

    def _active_event_name(self, guild_id: str, settings: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Return this guild's active custom event name, if any."""
        stored = (settings or {}).get("active") or {}
        if stored.get("name"):
            return stored["name"]
        task = self._tasks.get(guild_id)
        if task is not None and not task.done():
            activity = self.bot.presence_manager.activity_status.get(self._presence_key(guild_id))
            if activity and activity.get("text"):
                return activity["text"]
            return "event"
        activity = self.bot.presence_manager.activity_status.get(self._presence_key(guild_id))
        if activity and activity.get("guild") == str(guild_id) and activity.get("text"):
            return activity["text"]
        return None

    # ---------------- Notifications ----------------

    async def _notify_call_channels(
        self,
        activity_status: str,
        user: discord.abc.Snowflake,
        guild: discord.Guild,
        settings: Dict[str, Any],
        delay_seconds: int,
    ) -> None:
        guild_name = guild.name
        delay_text = self._format_delay(delay_seconds)
        for cid in settings.get("call_channel_ids", []):
            ch = self.bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(
                        f"@here {user.mention} called **{activity_status}** in {delay_text}!     From: {guild_name}"
                    )
                except Exception:
                    self._get_guild_logger(guild).warning(f"Failed to send notify in channel {cid}")

    async def _notify_cancel_or_complete(
        self,
        activity_status: str,
        user: discord.abc.Snowflake,
        guild: discord.Guild,
        settings: Dict[str, Any],
        is_cancel: bool,
    ) -> None:
        guild_name = guild.name
        message = (
            f"@here {user.mention} **{activity_status}** cancelled.     From: {guild_name}"
            if is_cancel
            else f"{user.mention} **{activity_status}** completed.     From: {guild_name}"
        )
        for cid in settings.get("call_channel_ids", []):
            ch = self.bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(message)
                except Exception:
                    self._get_guild_logger(guild).warning(
                        f"Failed to send cancel/complete notify in channel {cid}"
                    )

    async def _notify_timeout(
        self,
        activity_status: str,
        user: discord.abc.Snowflake,
        guild: discord.Guild,
        settings: Dict[str, Any],
    ) -> None:
        guild_name = guild.name
        for cid in settings.get("call_channel_ids", []):
            ch = self.bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(
                        f"{user.mention} Activity **{activity_status}** timed out!     From: {guild_name}"
                    )
                except Exception:
                    self._get_guild_logger(guild).warning(f"Failed to send timeout notify in channel {cid}")

    # ---------------- Cancel menus ----------------

    async def _create_cancel_menus(
        self,
        activity_status: str,
        settings: Dict[str, Any],
        guild: discord.Guild,
        delay_seconds: int,
    ) -> Dict[str, int]:
        command_channel_ids = settings.get("command_channel_ids", [])
        cancel_map: Dict[str, int] = {}
        guild_logger = self._get_guild_logger(guild)
        delay_text = self._format_delay(delay_seconds)

        for cid in command_channel_ids:
            ch = self.bot.get_channel(cid)
            if not isinstance(ch, discord.TextChannel):
                continue
            try:
                embed_cancel = discord.Embed(
                    title="Custom Event Call Active",
                    description=(
                        f"**In-Game Message**\n{activity_status} in {delay_text}\nCancel {activity_status}\n\n"
                        f"Current activity: **{activity_status}**\n"
                        f"❌ Cancel with notification\n🔕 Clear silently (Completed)"
                    ),
                    color=discord.Color.gold(),
                )
                cancel_msg = await ch.send(embed=embed_cancel)
                for e in cancel_emojis:
                    try:
                        await cancel_msg.add_reaction(e)
                    except Exception:
                        pass
                cancel_map[str(ch.id)] = cancel_msg.id
            except Exception:
                guild_logger.warning(f"Failed to create cancel message in channel {cid}")

        return cancel_map

    async def _cleanup_cancel_messages(
        self,
        settings: Dict[str, Any],
        guild: Optional[discord.Guild],
        guild_id: Optional[str] = None,
    ) -> None:
        cancel_map = settings.get("cancel_message_ids", {}) or {}
        for ch_id_str, msg_id in list(cancel_map.items()):
            ch = self.bot.get_channel(int(ch_id_str))
            if ch:
                await self._safe_delete_message(ch, msg_id)
            cancel_map.pop(ch_id_str, None)
        settings["cancel_message_ids"] = cancel_map
        settings["active"] = None
        storage_guild_id = str(guild.id) if guild else guild_id
        if storage_guild_id is None:
            raise ValueError("A guild or guild ID is required to clean up cancel messages.")
        await self._save_event_state(storage_guild_id, settings)

    # ---------------- Cog lifecycle ----------------

    async def cog_load(self) -> None:
        coll = self.db._get_collection("eventcall")
        for guild_id, settings in list(coll.items()):
            if not isinstance(settings, dict):
                await self.db.set("eventcall", guild_id, {}, save=True)
                continue
            updated = False
            for key, default in (
                ("command_channel_ids", []),
                ("call_channel_ids", []),
                ("allowed_roles", []),
                ("cancel_message_ids", {}),
            ):
                if key not in settings:
                    settings[key] = default
                    updated = True
            if updated:
                await self.db.set("eventcall", guild_id, settings, save=True)

        await self._resume_active_events()

        if hasattr(self.bot, "logger"):
            self.bot.logger.base_logger.info("EventCall: settings loaded and verified successfully.")
        else:
            print("[EventCall] Settings loaded and verified successfully.")

    async def _resume_active_events(self) -> None:
        coll = self.db._get_collection("eventcall")
        resumed = 0
        for guild_id, raw in list(coll.items()):
            if not isinstance(raw, dict):
                continue
            active = raw.get("active")
            if not active or not isinstance(active, dict):
                continue
            name = active.get("name")
            timeout = int(active.get("timeout") or 0)
            user_id = active.get("user_id")
            started_at = active.get("started_at")
            if not name or timeout <= 0:
                continue

            remaining = float(timeout)
            if started_at:
                try:
                    started = datetime.fromisoformat(started_at)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    remaining = timeout - (datetime.now(timezone.utc) - started).total_seconds()
                except (TypeError, ValueError):
                    remaining = 0

            self.bot.presence_manager.set_activity(
                self._presence_key(str(guild_id)), name, priority=10, activity_guild=str(guild_id)
            )
            user = None
            if user_id:
                user = self.bot.get_user(int(user_id))
            delay = max(0, remaining)
            task = asyncio.create_task(self._event_timer_task(str(guild_id), name, user, int(delay) if delay > 0 else 0))
            self._tasks[str(guild_id)] = task
            resumed += 1

        if resumed and hasattr(self.bot, "logger"):
            self.bot.logger.base_logger.info(f"EventCall: resumed {resumed} active event(s) after restart.")

    async def cog_unload(self) -> None:
        for t in list(self._tasks.values()):
            try:
                t.cancel()
            except Exception:
                pass
        self._tasks.clear()
        try:
            pm = self.bot.presence_manager
            for key in list(pm.activity_status):
                if key == ACTIVITY_KEY or key.startswith(f"{ACTIVITY_KEY}:"):
                    pm.clear_activity(key)
        except Exception:
            pass
        coll = self.db._get_collection("eventcall")
        for guild_id_str, raw in list(coll.items()):
            if not isinstance(raw, dict):
                continue
            try:
                settings = await self._load_settings(guild_id_str)
                guild = self.bot.get_guild(int(guild_id_str))
                if guild:
                    await self._cleanup_cancel_messages(settings, guild)
            except Exception:
                pass

    # ---------------- Core start / stop ----------------

    def _validate_minutes(self, minutes: int) -> Optional[str]:
        if minutes < MIN_MINUTES:
            return f"Delay must be at least {MIN_MINUTES} minute."
        if minutes > MAX_MINUTES:
            return f"Delay cannot exceed {MAX_MINUTES} minutes (24 hours)."
        return None

    async def _start_event_call(
        self,
        source: Source,
        user: discord.User | discord.Member,
        guild: discord.Guild,
        name: str,
        minutes: int,
    ) -> None:
        name = sanitize_event_name(name)
        if not name:
            await self._respond(source, "⚠️ Please provide an event name.", ephemeral=True)
            return

        err = self._validate_minutes(minutes)
        if err:
            await self._respond(source, f"⚠️ {err}", ephemeral=True)
            return

        guild_id = str(guild.id)
        settings = await self._load_settings(guild_id)

        if not settings.get("call_channel_ids"):
            await self._respond(
                source,
                "❌ No call channels configured. An admin must run `set_activity_call_channels` "
                "or `set_event_call_channels` first.",
                ephemeral=True,
            )
            return

        if not await self._check_user_permission(user, guild, settings):
            await self._respond(
                source,
                "❌ You do not have permission to call an event. Ask an admin to add your role with "
                "`set_allowed_roles` or `set_event_allowed_roles`.",
                ephemeral=True,
            )
            return

        delay_seconds = minutes * 60
        delay_text = self._format_delay(delay_seconds)

        async with self._guild_lock(guild_id):
            existing = self._active_event_name(guild_id, settings)
            if existing:
                await self._respond(
                    source,
                    f"⚠️ A custom event **{existing}** is already active in this server. "
                    f"Use `eventcancel` or `eventdone` first.",
                    ephemeral=True,
                )
                return

            self.bot.presence_manager.set_activity(
                self._presence_key(guild_id), name, priority=10, activity_guild=guild_id
            )

            await self._notify_call_channels(name, user, guild, settings, delay_seconds)

            cancel_map = await self._create_cancel_menus(name, settings, guild, delay_seconds)
            settings["cancel_message_ids"] = cancel_map
            settings["active"] = {
                "name": name,
                "user_id": user.id,
                "timeout": delay_seconds,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            await self._save_event_state(guild_id, settings)

            if task := self._tasks.pop(guild_id, None):
                task.cancel()
            task = asyncio.create_task(self._event_timer_task(guild_id, name, user, delay_seconds))
            self._tasks[guild_id] = task

        confirm = f"✅ Called **{name}** in {delay_text}."
        # Avoid a second public message in a call channel; slash confirms stay ephemeral.
        call_ids = set(settings.get("call_channel_ids", []))
        channel_id = None
        if isinstance(source, commands.Context):
            channel_id = source.channel.id if source.channel else None
        elif isinstance(source, discord.Interaction):
            channel_id = source.channel_id

        if isinstance(source, discord.Interaction) or channel_id not in call_ids:
            await self._respond(source, confirm, ephemeral=True)
        elif isinstance(source, commands.Context):
            try:
                await source.message.add_reaction("✅")
            except Exception:
                pass

        self._get_guild_logger(guild).info(
            f"{user} started custom event call '{name}' ({delay_text}) in guild {guild_id}"
        )

    async def _stop_event_call(
        self,
        source: Optional[Source],
        user: discord.abc.Snowflake,
        guild: discord.Guild,
        *,
        is_cancel: bool,
    ) -> None:
        guild_id = str(guild.id)
        settings = await self._load_settings(guild_id)
        guild_logger = self._get_guild_logger(guild)

        async with self._guild_lock(guild_id):
            activity = self.bot.presence_manager.activity_status.get(self._presence_key(guild_id))
            stored = settings.get("active") or {}
            if not activity and not stored:
                if source is not None:
                    await self._respond(source, "⚠️ No custom event call is active.", ephemeral=True)
                return

            activity_status = (activity or {}).get("text") or stored.get("name") or "event"
            await self._notify_cancel_or_complete(activity_status, user, guild, settings, is_cancel)

            if task := self._tasks.pop(guild_id, None):
                task.cancel()

            self.bot.presence_manager.clear_activity(self._presence_key(guild_id), guild_id)
            await self._cleanup_cancel_messages(settings, guild)
            await self.bot.presence_manager.force_update()

        action = "cancelled" if is_cancel else "completed"
        if source is not None:
            await self._respond(source, f"✅ **{activity_status}** {action}.", ephemeral=True)
        guild_logger.info(
            f"Custom event call {activity_status} {action} by {getattr(user, 'id', None)} in guild {guild_id}"
        )

    # ---------------- Prefix + slash commands ----------------

    @commands.command(name="eventcall", aliases=["ecall", "customcall"])
    @commands.guild_only()
    async def eventcall_prefix(self, ctx: commands.Context, *, details: str):
        """Call a custom event.

        Examples:
          !eventcall Faction VIP
          !eventcall Dungeon in 20
          !eventcall 2h Treasure Map
        """
        try:
            name, minutes = parse_event_call_args(details)
        except ValueError as exc:
            await ctx.send(f"⚠️ {exc}")
            return
        await self._start_event_call(ctx, ctx.author, ctx.guild, name, minutes)

    @app_commands.command(name="eventcall", description="Call a custom named event (no menu — type the name).")
    @app_commands.guild_only()
    @app_commands.describe(
        name="Event name to call (e.g. Faction VIP, Dungeon)",
        minutes="Minutes until the event starts (default 15)",
    )
    async def eventcall_slash(
        self,
        interaction: discord.Interaction,
        name: str,
        minutes: app_commands.Range[int, MIN_MINUTES, MAX_MINUTES] = DEFAULT_MINUTES,
    ):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        await self._start_event_call(interaction, interaction.user, interaction.guild, name, int(minutes))

    @commands.command(name="eventcancel")
    @commands.guild_only()
    async def eventcancel_prefix(self, ctx: commands.Context):
        """Cancel the active custom event call (notifies call channels)."""
        settings = await self._load_settings(str(ctx.guild.id))
        if not await self._check_user_permission(ctx.author, ctx.guild, settings):
            await ctx.send("❌ You do not have permission to cancel an event call.")
            return
        await self._stop_event_call(ctx, ctx.author, ctx.guild, is_cancel=True)

    @app_commands.command(name="eventcancel", description="Cancel the active custom event call.")
    @app_commands.guild_only()
    async def eventcancel_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        settings = await self._load_settings(str(interaction.guild.id))
        if not await self._check_user_permission(interaction.user, interaction.guild, settings):
            await interaction.followup.send(
                "❌ You do not have permission to cancel an event call.", ephemeral=True
            )
            return
        await self._stop_event_call(interaction, interaction.user, interaction.guild, is_cancel=True)

    @commands.command(name="eventdone", aliases=["eventcomplete"])
    @commands.guild_only()
    async def eventdone_prefix(self, ctx: commands.Context):
        """Mark the active custom event as completed (no cancel ping)."""
        settings = await self._load_settings(str(ctx.guild.id))
        if not await self._check_user_permission(ctx.author, ctx.guild, settings):
            await ctx.send("❌ You do not have permission to complete an event call.")
            return
        await self._stop_event_call(ctx, ctx.author, ctx.guild, is_cancel=False)

    @app_commands.command(name="eventdone", description="Mark the active custom event as completed.")
    @app_commands.guild_only()
    async def eventdone_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
            return
        settings = await self._load_settings(str(interaction.guild.id))
        if not await self._check_user_permission(interaction.user, interaction.guild, settings):
            await interaction.followup.send(
                "❌ You do not have permission to complete an event call.", ephemeral=True
            )
            return
        await self._stop_event_call(interaction, interaction.user, interaction.guild, is_cancel=False)

    @commands.command(name="eventstatus")
    @commands.guild_only()
    async def eventstatus_prefix(self, ctx: commands.Context):
        """Show the active custom event call, if any."""
        await self._send_status(ctx, ctx.guild)

    @app_commands.command(name="eventstatus", description="Show the active custom event call.")
    @app_commands.guild_only()
    async def eventstatus_slash(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self._send_status(interaction, interaction.guild)

    async def _send_status(self, source: Source, guild: discord.Guild) -> None:
        settings = await self._load_settings(str(guild.id))
        guild_id = str(guild.id)
        active = settings.get("active")
        presence = self.bot.presence_manager.activity_status.get(self._presence_key(guild_id))

        if not active and not presence:
            await self._respond(source, "No custom event call is active.", ephemeral=True)
            return

        name = (active or {}).get("name") or (presence or {}).get("text") or "event"
        timeout = int((active or {}).get("timeout") or 0)
        started_at = (active or {}).get("started_at")
        remaining_text = self._format_delay(timeout) if timeout else "unknown"
        if started_at and timeout:
            try:
                started = datetime.fromisoformat(started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                remaining = timeout - (datetime.now(timezone.utc) - started).total_seconds()
                remaining_text = self._format_delay(max(1, int(remaining))) if remaining > 0 else "ending now"
            except (TypeError, ValueError):
                pass

        caller = None
        user_id = (active or {}).get("user_id")
        if user_id:
            member = guild.get_member(int(user_id))
            caller = member.mention if member else f"<@{user_id}>"

        embed = discord.Embed(
            title="Custom Event Call",
            description=f"**{name}**",
            color=discord.Color.gold(),
        )
        if caller:
            embed.add_field(name="Called by", value=caller, inline=True)
        embed.add_field(name="Time remaining", value=remaining_text, inline=True)
        await self._respond(source, "", embed=embed, ephemeral=True)

    # ---------------- Setup commands ----------------

    @commands.hybrid_command(
        name="set_event_command_channel",
        description="Channel where custom-event cancel menus are posted. Falls back to the boss command channel if unset.",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def set_event_command_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if channel is None:
            await ctx.send("⚠️ You must mention a text channel (e.g., #channel-name).")
            return
        if channel.guild.id != ctx.guild.id:
            await ctx.send("⚠️ The channel must be from this server.")
            return
        if not channel.permissions_for(ctx.guild.me).view_channel:
            await ctx.send(f"⚠️ I don't have permission to view {channel.mention}.")
            return

        guild_id = str(ctx.guild.id)
        event = await self.db.get("eventcall", guild_id, {}) or {}
        if not isinstance(event, dict):
            event = {}
        event["command_channel_ids"] = [channel.id]
        event.setdefault("cancel_message_ids", {})
        await self.db.set("eventcall", guild_id, event, save=True)
        await ctx.send(f"✅ Event command channel saved: {channel.mention}")
        self._get_guild_logger(ctx.guild).info(f"{ctx.author} set event command channel: {channel.id}")

    @commands.hybrid_command(
        name="set_event_call_channels",
        description="Channel(s) that receive custom event pings. Falls back to boss call channels if unset.",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def set_event_call_channels(self, ctx: commands.Context, *, channels: str = None):
        if channels is None or not channels.strip():
            await ctx.send("⚠️ You must mention or provide at least one text channel ID.")
            return

        resolved_channels = await DiscordConverter.resolve_multiple_channels(self.bot, channels, ctx.guild)
        resolved_channels = [ch for ch in resolved_channels if ch.guild == ctx.guild]
        if not resolved_channels:
            await ctx.send("⚠️ No valid channels from this server could be resolved from your input.")
            return

        guild_id = str(ctx.guild.id)
        event = await self.db.get("eventcall", guild_id, {}) or {}
        if not isinstance(event, dict):
            event = {}
        event["call_channel_ids"] = [ch.id for ch in resolved_channels]
        await self.db.set("eventcall", guild_id, event, save=True)

        channel_mentions = " ".join(ch.mention for ch in resolved_channels)
        await ctx.send(f"✅ Event call/notification channels saved: {channel_mentions}")
        self._get_guild_logger(ctx.guild).info(
            f"{ctx.author} set event call channels: {[c.id for c in resolved_channels]}"
        )

    @commands.hybrid_command(
        name="set_event_allowed_roles",
        description="Roles that can run custom event calls. Falls back to boss allowed roles if unset.",
    )
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def set_event_allowed_roles(self, ctx: commands.Context, *, roles: str = None):
        if roles is None or not roles.strip():
            await ctx.send("⚠️ Please mention or provide at least one role ID.")
            return

        resolved_roles = await DiscordConverter.resolve_multiple_roles(self.bot, roles, ctx.guild)
        resolved_roles = [r for r in resolved_roles if r.guild == ctx.guild]
        if not resolved_roles:
            await ctx.send("⚠️ No valid roles from this server could be resolved from your input.")
            return

        guild_id = str(ctx.guild.id)
        event = await self.db.get("eventcall", guild_id, {}) or {}
        if not isinstance(event, dict):
            event = {}
        event["allowed_roles"] = [r.id for r in resolved_roles]
        await self.db.set("eventcall", guild_id, event, save=True)

        role_mentions = " ".join(r.mention for r in resolved_roles)
        await ctx.send(f"✅ Event allowed roles updated: {role_mentions}")
        self._get_guild_logger(ctx.guild).info(
            f"{ctx.author} set event allowed roles: {[r.id for r in resolved_roles]}"
        )

    # ---------------- Reaction handler (cancel menu only) ----------------

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User | discord.Member):
        if user.bot or reaction.message is None or reaction.message.guild is None:
            return

        guild = reaction.message.guild
        guild_id = str(guild.id)
        settings = await self._load_settings(guild_id)
        cancel_maps = settings.get("cancel_message_ids", {}) or {}
        if reaction.message.id not in cancel_maps.values():
            return

        if not await self._check_user_permission(user, guild, settings, dm_on_fail=True):
            await self._safe_remove_reaction(reaction, user)
            return

        emoji = str(reaction.emoji).strip()
        if emoji not in cancel_emojis:
            await self._safe_remove_reaction(reaction, user)
            return

        await self._stop_event_call(None, user, guild, is_cancel=(emoji == "❌"))

    # ---------------- Timer ----------------

    async def _event_timer_task(
        self,
        guild_id: str,
        activity_status: str,
        user: Optional[discord.User],
        timeout: int = 900,
    ):
        try:
            await asyncio.sleep(max(0, timeout))
            settings = await self._load_settings(guild_id)
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                target = user
                if target is None:
                    user_id = (settings.get("active") or {}).get("user_id")
                    if user_id:
                        target = self.bot.get_user(int(user_id))
                if target is not None:
                    await self._notify_timeout(activity_status, target, guild, settings)

            async with self._guild_lock(guild_id):
                self.bot.presence_manager.clear_activity(self._presence_key(guild_id), guild_id)
                await self._cleanup_cancel_messages(settings, guild, guild_id)
                self._tasks.pop(guild_id, None)
                await self.bot.presence_manager.force_update()

        except asyncio.CancelledError:
            async with self._guild_lock(guild_id):
                self._tasks.pop(guild_id, None)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(EventCall(bot))
