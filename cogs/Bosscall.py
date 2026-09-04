import math
import asyncio
import discord
from discord.ext import commands
from typing import Optional, Dict, Any
from utils.Converter import DiscordConverter
#
# TODO: Implement new menu (Faction VIP, dungeon, activity) global activity status most reaction change the current activity
# TODO: Add minipool for boss run yes/no
# TODO: bot status also display cancelled/completed boss
# TODO: Remove rework Ocean boss "🇴": "Mini Ocean's", "🇴": "Main Ocean's", "🇴": "Omni Boss", ,"🇴"

Boss_emojs = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","🇰","🇼"]
Mini_Boss_emojs = Boss_emojs.copy()
cancel_emojis = ["❌","🔕"]
other_emojs = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","🇨"]
            
TOWN_INFO = {
    "1️⃣": "Prevalia",
    "2️⃣": "Andaria",
    "3️⃣": "Cambria",
    "4️⃣": "Corpse Creek",
    "5️⃣": "Horseshoe Bay",
    "6️⃣": "Outpost",
    "7️⃣": "Shelter Island",
    "8️⃣": "Terran",
    "9️⃣": "Anchor's Rest",
}

OTHER_INFO = {
    "1️⃣": "Compassion",
    "2️⃣": "Honesty",
    "3️⃣": "Honor",
    "4️⃣": "Justice",
    "5️⃣": "Sacrifice",
    "6️⃣": "Valor",
    "7️⃣": "Humility",
    "8️⃣": "Spirituality",
    "🇨": "Trade Caravan",
}

BOSS_INFO = {
    "1️⃣": "Main Aegis",  "2️⃣": "Main Cavernam", "3️⃣": "Main Nusero",
    "4️⃣": "Main Ossuary",  "5️⃣": "Main Mausoleum",  "6️⃣": "Main Pulma",
    "7️⃣": "Main Inferno","8️⃣": "Main Darkmire","9️⃣": "Main Petram",
    "🔟": "Main Cathedral", "🇰": "Main Kraul Hive", "🇼": "Main Wilderness",
}
MINI_BOSS_INFO = {
    "1️⃣": "Mini Aegis", "2️⃣": "Mini Cavernam", "3️⃣": "Mini Nusero",
    "4️⃣": "Mini Ossuary","5️⃣": "Mini Mausoleum", "6️⃣": "Mini Pulma",
    "7️⃣": "Mini Inferno","8️⃣": "Mini Darkmire","9️⃣": "Mini Petram",
    "🔟": "Mini Cathedral","🇰": "Mini Kraul Hive", "🇼": "Mini Wilderness",
}

class BossCall(commands.Cog):
    """Boss call menu cog — multi-channel, persistent, and safe cleanup."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # expect bot.storage to exist (AsyncJSONStorage)
        self.db = bot.db  # shared DB
        self.presence_manager = bot.presence_manager 
        # runtime state

        self._tasks: Dict[str, asyncio.Task] = {}    # guild_id -> timer task
        self._locks: Dict[str, asyncio.Lock] = {}    # guild_id -> lock

    # ---------------- Helpers ----------------

    def _guild_lock(self, guild_id: str) -> asyncio.Lock:
        """Return a reusable per-guild lock to prevent race conditions."""
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
        import logging
        return logging.getLogger("BossCall")

    async def _safe_remove_reaction(self, reaction: discord.Reaction, user: discord.abc.Snowflake) -> bool:
        """
        Try to remove the user's reaction safely.
        Returns True if removed, False if not (e.g. no permission).
        """
        channel = reaction.message.channel
        guild = reaction.message.guild
        try:
            # Check if bot has Manage Messages permission in this channel
            bot_member = guild.get_member(self.bot.user.id)
            if bot_member and channel.permissions_for(bot_member).manage_messages:
                # use reaction.remove (preferred)
                try:
                    await reaction.remove(user)
                    return True
                except Exception:
                    # fallback to message.remove_reaction
                    try:
                        await reaction.message.remove_reaction(reaction.emoji, user)
                        return True
                    except Exception:
                        return False
            else:
                # no permission — cannot remove
                return False
        except Exception:
            # something unexpected — try best-effort removal then fail
            try:
                await reaction.remove(user)
                return True
            except Exception:
                return False

    async def _safe_delete_message(self, channel: discord.TextChannel, message_id: int) -> bool:
        """Fetch and delete a message if possible. Returns True if deleted or not found (treated as deleted)."""
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
            # not found = already gone; treat as success
            return True
        except Exception:
            return False

    # ---------------- Permission Check ----------------
    async def _check_user_permission(self, user: discord.User | discord.Member, guild: discord.Guild, settings: Dict[str, Any]) -> bool:
        """Check if the user has permission to trigger an activity call."""
        allowed_roles = set(settings.get("allowed_roles", []))
        if not allowed_roles:
            return False  # No roles specified

        if not hasattr(user, "roles"):
            try:
                await user.send("You do not have permission to call an activity.")
            except Exception:
                pass
            self._get_guild_logger(guild).warning(f"Non-member tried activity call: {getattr(user, 'id', None)}")
            return False

        user_roles = {r.id for r in user.roles}
        if not (user_roles & allowed_roles):
            try:
                await user.send("You do not have permission to call an activity.")
            except Exception:
                pass
            self._get_guild_logger(guild).warning(f"Unauthorized activity call attempt by {user}")
            return False

        return True

    # ---------------- Message Type Determination ----------------
    def _get_message_type(self, reaction: discord.Reaction, settings: Dict[str, Any]) -> Optional[str]:
        """Determine the type of message based on stored IDs. Returns 'main', 'mini', 'other', 'cancel', or None."""
        message_id = reaction.message.id
        reaction_maps = settings.get("reaction_message_ids", {}) or {}
        reaction_maps2 = settings.get("reaction_message_ids2", {}) or {}
        reaction_maps3 = settings.get("reaction_message_ids3", {}) or {}
        cancel_maps = settings.get("cancel_message_ids", {}) or {}

        if any(msg_id == message_id for msg_id in reaction_maps.values()):
            return "main"
        if any(msg_id == message_id for msg_id in reaction_maps2.values()):
            return "mini"
        if any(msg_id == message_id for msg_id in reaction_maps3.values()):
            return "other"
        if any(msg_id == message_id for msg_id in cancel_maps.values()):
            return "cancel"
        return None

    def _get_channel_key_for_cancel(self, reaction: discord.Reaction, settings: Dict[str, Any]) -> Optional[str]:
        """Get the channel key for a cancel message."""
        message_id = reaction.message.id
        cancel_maps = settings.get("cancel_message_ids", {}) or {}
        return next((k for k, v in cancel_maps.items() if v == message_id), None)

    # ---------------- Emoji Validation ----------------
    def _validate_emoji(self, emoji: str, msg_type: str) -> bool:
        """Validate the emoji against the expected set for the message type."""
        emoji = str(emoji).strip()
        if msg_type == "main" and emoji not in BOSS_INFO:
            return False
        if msg_type == "mini" and emoji not in MINI_BOSS_INFO:
            return False
        if msg_type == "other" and emoji not in OTHER_INFO:
            return False
        if msg_type == "cancel" and emoji not in cancel_emojis:
            return False
        return True

    def _get_activity_status(self, emoji: str, msg_type: str) -> Optional[str]:
        """Get the activity status from the emoji and message type."""
        emoji = str(emoji).strip()
        if msg_type == "main":
            return BOSS_INFO.get(emoji)
        elif msg_type == "mini":
            return MINI_BOSS_INFO.get(emoji)
        elif msg_type == "other":
            return OTHER_INFO.get(emoji)
        return None

    # ---------------- Global Activity Check ----------------
    async def _check_global_activity_conflict(self, reaction: discord.Reaction, user: discord.abc.Snowflake, guild: discord.Guild) -> bool:
        """Check for global activity conflict and notify if present. Returns True if conflict exists."""
        if not self.bot.presence_manager.has_activity("Bosscall"):
            return False

        activity = self.bot.presence_manager.activity_status["Bosscall"]
        active_status = activity["text"]
        active_guild_id = activity["guild"]
        active_guild = self.bot.get_guild(int(active_guild_id))
        active_guild_name = active_guild.name if active_guild else "another guild"

        await self._safe_remove_reaction(reaction, user)
        
        try:
            await user.send(f"An activity **{active_status}** is already active in {active_guild_name}.")
        except discord.HTTPException:
            pass
        self._get_guild_logger(guild).info(f"Blocked global duplicate activity call by {user} ({user.id})")
        return True

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

    # ---------------- Notifications ----------------
    async def _notify_call_channels(self, activity_status: str, user: discord.abc.Snowflake, guild: discord.Guild, settings: Dict[str, Any], delay_seconds: int) -> None:
        """Send notifications to call channels about the activity call."""
        guild_name = guild.name
        delay_text = self._format_delay(delay_seconds)
        for cid in settings.get("call_channel_ids", []):
            ch = self.bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(f"@here {user.mention} called **{activity_status}** in {delay_text}!     From: {guild_name}")
                except Exception:
                    self._get_guild_logger(guild).warning(f"Failed to send notify in channel {cid}")
                    
    async def _notify_cancel_or_complete(self, activity_status: str, user: discord.abc.Snowflake, guild: discord.Guild, settings: Dict[str, Any], is_cancel: bool) -> None:
        """Send notifications to call channels about cancel or complete."""
        guild_name = guild.name
        message = f"@here {user.mention} **{activity_status}** cancelled.     From: {guild_name}" if is_cancel else f"{user.mention} **{activity_status}** completed.     From: {guild_name}"
        for cid in settings.get("call_channel_ids", []):
            ch = self.bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(message)
                except Exception:
                    self._get_guild_logger(guild).warning(f"Failed to send cancel/complete notify in channel {cid}")

    async def _notify_timeout(self, activity_status: str, user: discord.abc.Snowflake, guild: discord.Guild, settings: Dict[str, Any]) -> None:
        """Send timeout notifications to call channels."""
        guild_name = guild.name
        for cid in settings.get("call_channel_ids", []):
            ch = self.bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(f"{user.mention} Activity **{activity_status}** timed out!     From: {guild_name}")
                except Exception:
                    self._get_guild_logger(guild).warning(f"Failed to send timeout notify in channel {cid}")

    # ---------------- Cancel Menu Creation ----------------
    async def _create_cancel_menus(self, activity_status: str, settings: Dict[str, Any], guild: discord.Guild, delay_seconds: int) -> Dict[str, int]:
        """Create cancel menus in command channels and return the updated map."""
        command_channel_ids = settings.get("command_channel_ids", [])
        cancel_map = settings.get("cancel_message_ids", {}) or {}
        guild_logger = self._get_guild_logger(guild)
        delay_text = self._format_delay(delay_seconds)

        for cid in command_channel_ids:
            ch = self.bot.get_channel(cid)
            if not isinstance(ch, discord.TextChannel):
                continue
            try:
                embed_cancel = discord.Embed(
                    title="Activity Call Active",
                    description=(
                        f"**In-Game Message**\n{activity_status} in {delay_text}\nCancel {activity_status}\n\n"
                        f"Current activity: **{activity_status}**\n❌ Cancel with notification\n🔕 Clear silently (Completed)"
                    ),
                    color=discord.Color.red(),
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

    # ---------------- Cleanup ----------------
    async def _cleanup_cancel_messages(
        self,
        settings: Dict[str, Any],
        guild: Optional[discord.Guild],
        guild_id: Optional[str] = None,
    ) -> None:
        """Delete all cancel messages and clear the map."""
        cancel_map = settings.get("cancel_message_ids", {}) or {}
        for ch_id_str, msg_id in list(cancel_map.items()):
            ch = self.bot.get_channel(int(ch_id_str))
            if ch:
                await self._safe_delete_message(ch, msg_id)
            cancel_map.pop(ch_id_str, None)
        settings["cancel_message_ids"] = cancel_map
        storage_guild_id = str(guild.id) if guild else guild_id
        if storage_guild_id is None:
            raise ValueError("A guild or guild ID is required to clean up cancel messages.")
        await self.db.set("bosscall", storage_guild_id, settings, save=True)

    # ---------------- Menu Creation Helpers ----------------
    def _create_menu_embed(self, title: str, description: str, info_dict: Dict[str, str]) -> discord.Embed:
        """Create an embed for a menu from the given info dictionary."""
        items = list(info_dict.items())
        mid = math.ceil(len(items) / 2)
        column1_items = items[:mid]
        column2_items = items[mid:]

        column1 = "\n".join(f"{emoji} - {name}" for emoji, name in column1_items)
        column2 = "\n".join(f"{emoji} - {name}" for emoji, name in column2_items)

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blue()
        )
        embed.add_field(name="\u200b", value=column1, inline=True)
        embed.add_field(name="\u200b", value=column2 if column2 else "\u200b", inline=True)
        return embed

    async def _send_menu_and_add_reactions(self, channel: discord.TextChannel, embed: discord.Embed, emojis: list) -> Optional[int]:
        """Send the embed to the channel, add reactions, and return the message ID."""
        try:
            msg = await channel.send(embed=embed)
            for e in emojis:
                try:
                    await msg.add_reaction(e)
                except Exception:
                    pass
            return msg.id
        except Exception:
            self._get_guild_logger(channel.guild).warning(f"Failed to post menu in channel {channel.id}")
            return None

    async def _cleanup_old_menus(self, settings: Dict[str, Any]) -> None:
        """Delete old menu messages from the stored maps."""
        maps_to_clean = [
            ("reaction_message_ids", {}),
            ("reaction_message_ids2", {}),
            ("reaction_message_ids3", {}),
            ("cancel_message_ids", {})
        ]
        for map_key, _ in maps_to_clean:
            msg_map: Dict[str, int] = settings.get(map_key, {}) or {}
            for ch_id_str, msg_id in list(msg_map.items()):
                try:
                    ch = self.bot.get_channel(int(ch_id_str))
                    if ch:
                        await self._safe_delete_message(ch, msg_id)
                except Exception:
                    pass

    async def _create_menus_in_channels(self, settings: Dict[str, Any], guild: discord.Guild) -> Dict[str, Dict[str, int]]:
        """Create all types of menus in command channels and return the new maps."""
        cmd_channel_ids = settings.get("command_channel_ids", [])
        new_maps = {
            "main": {},
            "mini": {},
            "other": {},
            "cancel": {}
        }

        for cid in cmd_channel_ids:
            ch = self.bot.get_channel(cid)
            if not isinstance(ch, discord.TextChannel):
                continue

            # Main Boss Menu
            embed_main = self._create_menu_embed(
                "Boss Call Menu",
                "Select a boss to call by reacting with the corresponding emoji.",
                BOSS_INFO
            )
            msg_id = await self._send_menu_and_add_reactions(ch, embed_main, Boss_emojs)
            if msg_id:
                new_maps["main"][str(ch.id)] = msg_id

            # Mini Boss Menu
            embed_mini = self._create_menu_embed(
                "Mini Boss Call Menu",
                "Select a mini-boss to call by reacting with the corresponding emoji.",
                MINI_BOSS_INFO
            )
            msg_id = await self._send_menu_and_add_reactions(ch, embed_mini, Mini_Boss_emojs)
            if msg_id:
                new_maps["mini"][str(ch.id)] = msg_id

            # Other Menu
            embed_other = self._create_menu_embed(
                "Other Call Menu",
                "Select to call by reacting with the corresponding emoji.",
                OTHER_INFO
            )
            msg_id = await self._send_menu_and_add_reactions(ch, embed_other, other_emojs)
            if msg_id:
                new_maps["other"][str(ch.id)] = msg_id

        return new_maps

    # ---------------- Cog lifecycle ----------------
    async def cog_load(self) -> None:
        """Ensure BossCall settings defaults exist per guild (DB is loaded in setup_hook)."""
        coll = self.db._get_collection("bosscall")

        updated = False
        for guild_id, settings in list(coll.items()):
            if not isinstance(settings, dict):
                settings = {}
                updated = True

            # Only add missing fields (don’t reset)
            if "command_channel_ids" not in settings:
                settings["command_channel_ids"] = []
                updated = True
            if "call_channel_ids" not in settings:
                settings["call_channel_ids"] = []
                updated = True
            if "allowed_roles" not in settings:
                settings["allowed_roles"] = []
                updated = True
            if "reaction_message_ids" not in settings:
                settings["reaction_message_ids"] = {}
                updated = True
            if "reaction_message_ids2" not in settings:
                settings["reaction_message_ids2"] = {}
                updated = True
            if "reaction_message_ids3" not in settings:
                settings["reaction_message_ids3"] = {}
                updated = True
            if "cancel_message_ids" not in settings:
                settings["cancel_message_ids"] = {}
                updated = True

            # If we modified anything, write it back
            if updated:
                await self.db.set("bosscall", guild_id, settings, save=True)
                updated = False  # reset flag for next guild

        if hasattr(self.bot, "logger"):
            self.bot.logger.base_logger.info("BossCall: settings loaded and verified successfully.")
        else:
            print("[BossCall] Settings loaded and verified successfully.")

    async def cog_unload(self) -> None:
        # cancel running timer tasks
        for t in list(self._tasks.values()):
            try:
                t.cancel()
            except Exception:
                pass
        self._tasks.clear()
        coll = self.db._get_collection("bosscall")
        for guild_id_str, settings in list(coll.items()):
            if isinstance(settings, dict):
                try:
                    guild_id = int(guild_id_str)
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        await self._cleanup_cancel_messages(settings, guild)
                except Exception:
                    pass
    # ---------------- Setup commands ----------------
    @commands.hybrid_command(name="set_command_channel", description="Define the channel where activity menus will be posted.")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def set_command_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set a single channel where activity menus will be posted."""
        if channel is None:
            await ctx.send("⚠️ You must mention a text channel (e.g., #channel-name).")
            return

        # Ensure the channel is from the current guild
        if channel.guild.id != ctx.guild.id:
            await ctx.send("⚠️ The channel must be from this server.")
            return

        # Check if the bot has permissions to view the channel
        if not channel.permissions_for(ctx.guild.me).view_channel:
            await ctx.send(f"⚠️ I don't have permission to view {channel.mention}.")
            return

        # Save the channel ID to the database
        guild_id = str(ctx.guild.id)
        settings = await self.db.get("bosscall", guild_id, {})
        settings["command_channel_ids"] = [channel.id]  # Store as a single-item list for consistency
        # Reset previous menu IDs (we will recreate on boss_menu)
        settings.setdefault("reaction_message_ids", {})
        settings.setdefault("reaction_message_ids2", {})
        settings.setdefault("reaction_message_ids3", {})
        settings.setdefault("cancel_message_ids", {})
        # Remove old IDs (optional)
        #settings["reaction_message_ids"].clear()
        #settings["reaction_message_ids2"].clear()
        #settings["reaction_message_ids3"].clear()
        #settings["cancel_message_ids"].clear()
        await self.db.set("bosscall", guild_id, settings, save=True)
        
        await ctx.send(f"✅ Command channel saved: {channel.mention}")
        self._get_guild_logger(ctx.guild).info(f"{ctx.author} set command channel: {channel.id}")

    @commands.hybrid_command(name="set_activity_call_channels", description="Define the channel(s) where activity notifications are sent.")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def set_activity_call_channels(self, ctx: commands.Context, *, channels: str = None):
        if channels is None or not channels.strip():
            await ctx.send("⚠️ You must mention or provide at least one text channel ID.")
            return

        resolved_channels = await DiscordConverter.resolve_multiple_channels(self.bot, channels, ctx.guild)
        resolved_channels = [ch for ch in resolved_channels if ch.guild == ctx.guild]
        if not resolved_channels:
            await ctx.send("⚠️ No valid channels from this server could be resolved from your input.")
            return

        # No resolution needed—channels are already TextChannel objects from current guild
        guild_id = str(ctx.guild.id)
        settings = await self.db.get("bosscall", guild_id, {})
        settings["call_channel_ids"] = [ch.id for ch in resolved_channels]
        await self.db.set("bosscall", guild_id, settings, save=True)

        channel_mentions = ' '.join(ch.mention for ch in resolved_channels)
        await ctx.send(f"✅ Call/notification channels saved: {channel_mentions}")

        self._get_guild_logger(ctx.guild).info(
            f"{ctx.author} set call channels: {[c.id for c in resolved_channels]}"
        )

    @commands.hybrid_command(name="set_allowed_roles", description="Define which roles can trigger activity calls.")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def set_allowed_roles(self, ctx: commands.Context, *, roles: str = None):
        """Set which roles can use the activity menu. Supports mentions and role IDs."""
        if roles is None or not roles.strip():
            await ctx.send("⚠️ Please mention or provide at least one role ID.")
            return

        resolved_roles = await DiscordConverter.resolve_multiple_roles(self.bot, roles, ctx.guild)
        resolved_roles = [r for r in resolved_roles if r.guild == ctx.guild]
        if not resolved_roles:
            await ctx.send("⚠️ No valid roles from this server could be resolved from your input.")
            return

        # No resolution needed—roles are already valid Role objects from current guild
        guild_id = str(ctx.guild.id)
        settings = await self.db.get("bosscall", guild_id, {})
        settings["allowed_roles"] = [r.id for r in resolved_roles]
        await self.db.set("bosscall", guild_id, settings, save=True)

        role_mentions = ' '.join(r.mention for r in resolved_roles)
        await ctx.send(f"✅ Allowed roles updated: {role_mentions}")

        self._get_guild_logger(ctx.guild).info(
            f"{ctx.author} set allowed roles: {[r.id for r in resolved_roles]}"
        )
    @commands.hybrid_command(name="activity_menu", description="Generate or refresh the activity call menu in all command channels.")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()  
    async def activity_menu(self, ctx: commands.Context):
        """Generate or refresh activity call menus in configured command channels."""
        guild_id = str(ctx.guild.id)
        settings = await self.db.get("bosscall", guild_id, {})
        cmd_channel_ids = settings.get("command_channel_ids", [])
        if not cmd_channel_ids:
            await ctx.send("❌ No command channels configured. Use `set_command_channel` first.")
            return

        # Cleanup old menus
        await self._cleanup_old_menus(settings)
        
        # Post current settings for call_channel_ids and allowed_roles
        call_channels = []
        for cid in settings.get("call_channel_ids", []):
            ch = self.bot.get_channel(cid)
            if ch:
                call_channels.append(ch.mention)
            else:
                call_channels.append(f"<#{cid}>")
        call_mentions = " ".join(call_channels) if call_channels else "None"

        allowed_roles_list = []
        for rid in settings.get("allowed_roles", []):
            role = ctx.guild.get_role(rid)
            if role:
                allowed_roles_list.append(role.mention)
            else:
                allowed_roles_list.append(f"<@&{rid}>")
        roles_mentions = " ".join(allowed_roles_list) if allowed_roles_list else "None"

        settings_msg = f"**Current Settings:**\n**Call Channels:** {call_mentions}\n**Allowed Roles:** {roles_mentions}"
        await ctx.send(settings_msg)

        # Create new menus
        new_maps = await self._create_menus_in_channels(settings, ctx.guild)

        # Update settings with new maps
        settings["reaction_message_ids"] = new_maps["main"]
        settings["reaction_message_ids2"] = new_maps["mini"]
        settings["reaction_message_ids3"] = new_maps["other"]
        settings["cancel_message_ids"] = new_maps["cancel"]  # Empty for now
        await self.db.set("bosscall", guild_id, settings, save=True)

        await ctx.send("✅ Activity menus created/updated in command channels.")
        self._get_guild_logger(ctx.guild).info(f"{ctx.author} created/updated activity menus.")

    # ---------------- Reaction handler ----------------

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User | discord.Member):
        if user.bot or reaction.message is None or reaction.message.guild is None:
            return

        guild = reaction.message.guild
        guild_id = str(guild.id)
        settings = await self.db.get("bosscall", guild_id, {})

        # Only handle reactions on activity menus / cancel messages
        msg_type = self._get_message_type(reaction, settings)
        if msg_type is None:
            return

        # Permission check (only for actual activity-menu reactions)
        if not await self._check_user_permission(user, guild, settings):
            await self._safe_remove_reaction(reaction, user)
            return

        emoji = str(reaction.emoji).strip()
        if not self._validate_emoji(emoji, msg_type):
            await self._safe_remove_reaction(reaction, user)
            return
        
        if msg_type == "other" and emoji == "🇴": # omni timer is 1H ++
            timer = 3600
        else:
            timer = 900  # default 15 minutes
            
        # Handle cancel
        if msg_type == "cancel":
            channel_key = self._get_channel_key_for_cancel(reaction, settings)
            if channel_key:
                await self._handle_cancel(guild_id, guild, emoji, user, channel_key, settings)
            return

        # Handle activity call
        async with self._guild_lock(guild_id):
            if await self._check_global_activity_conflict(reaction, user, guild):
                return

            activity_status = self._get_activity_status(emoji, msg_type)
            if not activity_status:
                await self._safe_remove_reaction(reaction, user)
                return

            # Mark boss active globally
            self.bot.presence_manager.set_activity("Bosscall", activity_status, priority=10, activity_guild=guild_id)
            await self._safe_remove_reaction(reaction, user)

            # Notify call channels
            await self._notify_call_channels(activity_status, user, guild, settings, timer)

            # Create cancel menus
            cancel_map = await self._create_cancel_menus(activity_status, settings, guild, timer)
            settings["cancel_message_ids"] = cancel_map
            await self.db.set("bosscall", guild_id, settings, save=True)

            # Start timer
            if task := self._tasks.pop(guild_id, None):
                task.cancel()
            task = asyncio.create_task(self._boss_timer_task(guild_id, activity_status, user, timer))
            self._tasks[guild_id] = task

    # ---------------- Cancel handling ----------------

    async def _handle_cancel(self, guild_id: str, guild: discord.Guild, emoji: str, user: discord.abc.Snowflake, channel_key: Optional[str], settings: Dict[str, Any]):
        guild_logger = self._get_guild_logger(guild)
        async with self._guild_lock(guild_id):
            activity = self.bot.presence_manager.activity_status.pop("Bosscall", None)
            if not activity:
                guild_logger.warning(f"No active activity to cancel in {guild_id}")
                return

            activity_status = activity["text"]
            is_cancel = emoji == "❌"
            await self._notify_cancel_or_complete(activity_status, user, guild, settings, is_cancel)

            # Cancel running timer
            if task := self._tasks.pop(guild_id, None):
                task.cancel()

            # Delete cancel messages
            await self._cleanup_cancel_messages(settings, guild)

            action = "cancelled" if is_cancel else "completed"
            guild_logger.info(f"Activity call {activity_status} {action} by {getattr(user, 'id', None)} in guild {guild_id}")

            await self.bot.presence_manager.force_update()

    # ---------------- Timer ----------------
    async def _boss_timer_task(self, guild_id: str, activity_status: str, user: discord.User, timeout: int = 900):
        try:
            await asyncio.sleep(timeout)
            settings = await self.db.get("bosscall", guild_id, {})
            guild = self.bot.get_guild(int(guild_id))
            if guild:
                await self._notify_timeout(activity_status, user, guild, settings)

            async with self._guild_lock(guild_id):
                self.bot.presence_manager.clear_activity("Bosscall", guild_id)
                await self._cleanup_cancel_messages(settings, guild, guild_id)
                self._tasks.pop(guild_id, None)
                await self.bot.presence_manager.force_update()

        except asyncio.CancelledError:
            async with self._guild_lock(guild_id):
                self.bot.presence_manager.clear_activity("Bosscall", guild_id)
                self._tasks.pop(guild_id, None)
                await self.bot.presence_manager.force_update()
            return

async def setup(bot: commands.Bot):
    await bot.add_cog(BossCall(bot))