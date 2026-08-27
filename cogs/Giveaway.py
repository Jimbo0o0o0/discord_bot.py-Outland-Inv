import discord
from discord.ext import commands
import asyncio
import random
import json
import io
from datetime import datetime, timedelta, timezone
from typing import List
from utils.utils import DiscordConverter

# Start a thread for giveaway management
# Edit main message when giveaway ends
# Remove button after giveaway ends and replace by clear button to remove the thread


EMBED_COLOR_ACTIVE = 0x00ff00
EMBED_COLOR_ENDED = 0xff0000


class GiveawayView(discord.ui.View):
    def __init__(self, cog: 'GiveawayCog'):
        super().__init__(timeout=None)
        self.cog = cog

    async def update_entries_field(self, message: discord.Message, entrants: list[str]):
        embed = message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name.lower() == "entries":
                embed.set_field_at(i, name="Entries", value=len(entrants), inline=False)
                break
        await message.edit(embed=embed)

    @discord.ui.button(label="Join Giveaway", style=discord.ButtonStyle.green, emoji="✅", custom_id="gw_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = str(interaction.message.id)
        user_id = str(interaction.user.id)
        data = await self.cog.storage.get("giveaways", msg_id)

        if not data or data.get("status") != "active":
            return await interaction.response.send_message("❌ This giveaway is no longer active.", ephemeral=True)
        if user_id in data["entrants"]:
            return await interaction.response.send_message("You have already joined this giveaway.", ephemeral=True)

        data["entrants"].append(user_id)
        await self.cog.storage.set("giveaways", msg_id, data)
        await self.update_entries_field(interaction.message, data["entrants"])

        try:
            await interaction.user.send(f"🎉 You joined the giveaway: **{data['title']}**")
        except discord.HTTPException:
            pass

        await interaction.response.send_message("✅ You joined the giveaway!", ephemeral=True)

    @discord.ui.button(label="Leave Giveaway", style=discord.ButtonStyle.red, emoji="❌", custom_id="gw_leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = str(interaction.message.id)
        user_id = str(interaction.user.id)
        data = await self.cog.storage.get("giveaways", msg_id)

        if not data or data.get("status") != "active":
            return await interaction.response.send_message("❌ This giveaway is no longer active.", ephemeral=True)
        if user_id not in data["entrants"]:
            return await interaction.response.send_message("You are not part of this giveaway.", ephemeral=True)

        data["entrants"].remove(user_id)
        await self.cog.storage.set("giveaways", msg_id, data)
        await self.update_entries_field(interaction.message, data["entrants"])

        try:
            await interaction.user.send(f"❌ You left the giveaway: **{data['title']}**")
        except discord.HTTPException:
            pass

        await interaction.response.send_message("You left the giveaway.", ephemeral=True)


class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = bot.db  # AsyncJSONStorage
        self.bot.add_view(GiveawayView(self))
        self._active_tasks = {}  # msg_id -> asyncio.Task

    async def cog_load(self):
        """Called automatically when cog is loaded (after bot starts)."""
        await self.resume_active_giveaways()

    async def _pick_winners(self, entrants: List[str], count: int) -> List[str]:
        if len(entrants) < count:
            return []
        return random.sample(entrants, count)

    async def _disable_buttons(self, message: discord.Message):
        view = GiveawayView(self)
        for item in view.children:
            item.disabled = True
        await message.edit(view=view)

    @commands.group(name="giveaway", invoke_without_command=True)
    async def giveaway(self, ctx: commands.Context):
        await ctx.send_help(ctx.command)

    @giveaway.command(name="create")
    @commands.has_permissions(manage_messages=True)
    async def create_giveaway(
        self, ctx: commands.Context, duration: int, winners: int, title: str,
        channel_input: str = None, *, description: str = ""
    ):
        if winners < 1:
            return await ctx.send("You must have at least one winner.")

        duration_seconds = duration * 60
        channel = await DiscordConverter.resolve_channel(self.bot, channel_input, ctx.guild) if channel_input else ctx.channel
        if not channel:
            return await ctx.send("Invalid channel.")

        now = datetime.now(timezone.utc)
        end_time = now + timedelta(seconds=duration_seconds)

        embed = discord.Embed(title=title, description=description or "No description.", color=EMBED_COLOR_ACTIVE)
        embed.add_field(name="Host", value=ctx.author.mention)
        embed.add_field(name="Winners", value=winners)
        embed.add_field(name="Ends At", value=discord.utils.format_dt(end_time, "R"))
        embed.add_field(name="Entries", value="0")

        view = GiveawayView(self)
        msg = await channel.send(embed=embed, view=view)
        data = {
            "title": title,
            "description": description,
            "duration": duration_seconds,
            "winners_count": winners,
            "host_id": ctx.author.id,
            "channel_id": channel.id,
            "guild_id": ctx.guild.id,
            "entrants": [],
            "status": "active",
            "start_time": now.isoformat(),
            "end_time": end_time.isoformat(),
        }

        msg_id = str(msg.id)
        await self.storage.set("giveaways", msg_id, data)
        await ctx.send(f"✅ Giveaway **{title}** started in {channel.mention} and ends {discord.utils.format_dt(end_time, 'R')}.")

        self._schedule_giveaway_end(msg_id, data)

    @giveaway.command(name="reroll")
    async def reroll_giveaway(self, ctx: commands.Context, message_id: int):
        msg_id = str(message_id)
        data = await self.storage.get("giveaways", msg_id)
        if not data or data.get("status") != "ended":
            return await ctx.send("❌ That giveaway is not ended or doesn’t exist.")
        if ctx.author.id != data["host_id"] and not ctx.author.guild_permissions.administrator:
            return await ctx.send("You don't have permission to reroll this giveaway.")

        winners = await self._pick_winners(data["entrants"], data["winners_count"])
        if not winners:
            return await ctx.send("❌ Not enough entrants to reroll.")

        data["winners"] = winners
        await self.storage.set("giveaways", msg_id, data)
        await self._announce_winners(data, msg_id, reroll=True)
        await ctx.send("✅ Giveaway rerolled successfully!")

    @giveaway.command(name="cancel")
    @commands.has_permissions(manage_messages=True)
    async def cancel_giveaway(self, ctx: commands.Context, message_id: int):
        msg_id = str(message_id)
        data = await self.storage.get("giveaways", msg_id)
        if not data or data.get("status") != "active":
            return await ctx.send("❌ This giveaway is not active.")
        if ctx.author.id != data["host_id"] and not ctx.author.guild_permissions.administrator:
            return await ctx.send("You can’t cancel this giveaway.")

        data["status"] = "cancelled"
        await self.storage.set("giveaways", msg_id, data)

        task = self._active_tasks.pop(msg_id, None)
        if task:
            task.cancel()

        channel = self.bot.get_channel(data["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await self._disable_buttons(msg)
            except discord.NotFound:
                pass
            await channel.send(embed=discord.Embed(
                title="🚫 Giveaway Cancelled",
                description=f"The giveaway **{data['title']}** was cancelled by {ctx.author.mention}.",
                color=EMBED_COLOR_ENDED
            ))
        await ctx.send("✅ Giveaway cancelled.")

    async def _announce_winners(self, data: dict, msg_id: str, reroll: bool = False):
        channel = self.bot.get_channel(data["channel_id"])
        if not channel:
            return

        winners = []
        for user_id in data.get("winners", []):
            try:
                user = await self.bot.fetch_user(int(user_id))
                winners.append(user)
                await user.send(f"🎉 You won the giveaway '{data['title']}'! Check it out: https://discord.com/channels/{data['guild_id']}/{data['channel_id']}/{msg_id}")
            except discord.HTTPException:
                pass

        winner_mentions = ", ".join(u.mention for u in winners) or "No valid winners."
        embed = discord.Embed(
            title="🎉 Giveaway Rerolled!" if reroll else "🎉 Giveaway Ended!",
            description=f"Prize: **{data['title']}**\nWinners: {winner_mentions}",
            color=EMBED_COLOR_ACTIVE
        )
        await channel.send(embed=embed)

    async def _end_giveaway(self, msg_id: str, delay: float):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return  # giveaway cancelled

        data = await self.storage.get("giveaways", msg_id)
        if not data or data.get("status") != "active":
            return

        data["status"] = "ended"
        winners = await self._pick_winners(data["entrants"], data["winners_count"])
        data["winners"] = winners
        await self.storage.set("giveaways", msg_id, data)

        channel = self.bot.get_channel(data["channel_id"])
        if channel:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await self._disable_buttons(msg)
            except discord.NotFound:
                pass

        await self._announce_winners(data, msg_id)
        await self._send_transparency_file(channel, msg_id, data)
        self._active_tasks.pop(msg_id, None)

    async def _send_transparency_file(self, channel: discord.TextChannel, msg_id: str, data: dict):
        file_data = json.dumps(data, indent=4).encode("utf-8")
        file = discord.File(io.BytesIO(file_data), filename=f"giveaway_{msg_id}.json")
        await channel.send("📜 Giveaway transparency data:", file=file)

    def _schedule_giveaway_end(self, msg_id: str, data: dict):
        """Starts the background countdown for a giveaway."""
        start = datetime.fromisoformat(data["start_time"])
        end = datetime.fromisoformat(data["end_time"])
        now = datetime.now(timezone.utc)
        delay = max((end - now).total_seconds(), 0)

        task = self.bot.loop.create_task(self._end_giveaway(msg_id, delay))
        self._active_tasks[msg_id] = task

    async def resume_active_giveaways(self):
        """Called on startup to resume active giveaways."""
        await self.storage.load()
        giveaways = await self.storage.all_dict("giveaways")

        resumed = 0
        for msg_id, data in giveaways.items():
            if data.get("status") == "active":
                end_time = datetime.fromisoformat(data["end_time"])
                now = datetime.now(timezone.utc)
                remaining = (end_time - now).total_seconds()

                if remaining <= 0:
                    # giveaway should have ended already
                    self.bot.loop.create_task(self._end_giveaway(msg_id, 0))
                else:
                    self._schedule_giveaway_end(msg_id, data)
                    resumed += 1

        print(f"[Giveaway] Resumed {resumed} active giveaways after restart.")


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
