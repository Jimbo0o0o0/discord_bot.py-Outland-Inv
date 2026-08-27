from email.mime import message
import discord
from discord.ext import commands
import aiohttp
from typing import Dict, List
from utils.utils import DiscordConverter


MAX_MAP_ENTRIES = 2000  # per channel limi
class WebhookChannelSync(commands.Cog):
    """Cross-guild multi-channel sync via webhooks (messages, edits, deletions)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = bot.db  # AsyncJSONStorage instance
        self.session = aiohttp.ClientSession()
        # message_map[src_channel_id][src_msg_id] = {target_channel_id: target_msg_id}
        self.message_map: Dict[int, Dict[int, Dict[int, int]]] = {}

    # ------------------------------------------------------------
    # 🔗 LINK MULTIPLE CHANNELS
    # ------------------------------------------------------------
    @commands.command(name="link_webhooks", description="Link multiple channels together for webhook syncing.")
    @commands.has_permissions(administrator=True)
    async def link_webhooks(self, ctx: commands.Context, *, channels_input: str):
        """
        Link multiple channels (mention, name, or raw ID) together via webhooks.
        Example:
          !link_webhooks #general #chat
          !link_webhooks 123456789012345678 987654321098765432
        """
        channels: List[discord.TextChannel] = await DiscordConverter.resolve_multiple_channels(self.bot, channels_input)

        if len(channels) < 2:
            await ctx.send("❌ Please specify at least two valid channels to link.")
            return

        # Load current mapping
        groups = await self.storage.get("sync_webhooks", "groups", default={})

        # Create or fetch webhook per channel
        channel_webhooks = {}
        for ch in channels:
            wh = None
            try:
                existing = await ch.webhooks()
                wh = next((w for w in existing if w.user == self.bot.user), None)
                if not wh:
                    wh = await ch.create_webhook(name=f"sync-{self.bot.user.name}")
            except discord.Forbidden:
                await ctx.send(f"⚠️ Missing permission to create webhook in {ch.mention}. Skipping.")
                continue
            channel_webhooks[ch.id] = wh.url

        # Merge all given channels into a sync group
        for ch in channels:
            partners = [url for cid, url in channel_webhooks.items() if cid != ch.id]
            existing = groups.get(str(ch.id), [])
            groups[str(ch.id)] = list(set(existing + partners))

        await self.storage.set("sync_webhooks", "groups", groups)

        # Try to update channel descriptions
        for ch in channels:
            try:
                # Example: 🔗 Synced with: #chat, #logs
                others = [f"#{c.name} ({c.guild.name})" for c in channels if c.id != ch.id]
                topic_text = f"🔗 Synced with: {', '.join(others)}"
                await ch.edit(topic=topic_text)
            except discord.Forbidden:
                await ctx.send(f"⚠️ Cannot edit topic for {ch.mention} (missing Manage Channels).")
            except Exception as e:
                print(f"[Webhook Sync] Error updating topic for {ch.id}: {e}")
        # Build pretty name list for display
        ch_names = [f"#{c.name} → ({c.guild.name})" for c in channels]
        joined_names = " ⇄ ".join(ch_names)

        await ctx.send(f"✅ Linked {len(channels)} channels together via webhooks!\n"
                       f"📢 Updated channel descriptions:\n{joined_names}")

    # ------------------------------------------------------------
    # 🔓 UNLINK CHANNEL
    # ------------------------------------------------------------
    @commands.command(name="unlink_webhook_channel", description="Unlink a channel from webhook sync.")
    @commands.has_permissions(administrator=True)
    async def unlink_webhook_channel(self, ctx: commands.Context, *, channel_input: str):
        """Unlinks a channel (mention, name, or ID) from the webhook sync network."""
        channel = await DiscordConverter.resolve_channel(self.bot, channel_input)
        if not channel:
            await ctx.send("❌ Invalid or not found channel.")
            return

        groups = await self.storage.get("sync_webhooks", "groups", default={})
        if str(channel.id) not in groups:
            await ctx.send("❌ This channel is not currently linked.")
            return

        groups.pop(str(channel.id))
        for ch_id, urls in list(groups.items()):
            groups[ch_id] = [u for u in urls if f"/{channel.id}/" not in u]
        await self.storage.set("sync_webhooks", "groups", groups)

        await ctx.send(f"🔗 Unlinked {channel.mention} from webhook sync.")

    # ------------------------------------------------------------
    # 📨 MESSAGE SYNC
    # ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.webhook_id or message.guild is None:
            return

        if not message.content and not message.embeds and not message.attachments:
            await message.channel.send(
                    f"⚠️ Sorry {message.author.mention}, messages types is not supported in synced channels.",
                    delete_after=5
                )
            return

        # 🚫 Block replies (webhooks can’t support them)
        if message.reference or message.type == discord.MessageType.reply:
            try:
                await message.delete()
                await message.channel.send(
                    f"⚠️ Sorry {message.author.mention}, replies and forwarded messages are not supported in synced channels.",
                    delete_after=5
                )
            except discord.Forbidden:
                pass
            return

        groups = await self.storage.get("sync_webhooks", "groups", default={})
        if str(message.channel.id) not in groups:
            return

        target_webhooks = groups[str(message.channel.id)]

        # Prepare files
        files = []
        if message.attachments:
            for a in message.attachments:
                try:
                    files.append(await a.to_file())
                except Exception as e:
                    print(f"[Webhook Sync] Failed to attach file: {e}")

        display_name = f" [{message.guild.name}] → {message.author.display_name}"
        embeds = message.embeds if message.embeds else []
        content = message.content or None

        self.message_map.setdefault(message.channel.id, {})

        for webhook_url in target_webhooks:
            try:
                webhook = discord.Webhook.from_url(webhook_url, session=self.session)
                sent_msg = await webhook.send(
                    content=content,
                    username=display_name[:80],
                    avatar_url=message.author.display_avatar.url,
                    embeds=embeds,   
                    files=files ,
                    wait=True
                )

                # Track for edits/deletes
                target_channel_id = int(webhook_url.split("/")[5])
                self.message_map[message.channel.id].setdefault(message.id, {})[target_channel_id] = sent_msg.id

                # Enforce message map limit
                
                if len(self.message_map[message.channel.id]) > MAX_MAP_ENTRIES:
                    oldest = next(iter(self.message_map[message.channel.id]))
                    del self.message_map[message.channel.id][oldest]

            except Exception as e:
                print(f"[Webhook Sync] Send error: {e}")

    # ------------------------------------------------------------
    # ✏️ EDIT SYNC
    # ------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or after.webhook_id or after.guild is None:
            return
        if before.content == after.content:
            return

        groups = await self.storage.get("sync_webhooks", "groups", default={})
        if str(after.channel.id) not in groups:
            return

        target_webhooks = groups[str(after.channel.id)]
        for webhook_url in target_webhooks:
            try:
                target_channel_id = int(webhook_url.split("/")[5])
                target_msg_id = self.message_map.get(after.channel.id, {}).get(after.id, {}).get(target_channel_id)
                if not target_msg_id:
                    continue
                webhook = discord.Webhook.from_url(webhook_url, session=self.session)
                await webhook.edit_message(target_msg_id, content=f"{after.content} *(edited)*")
            except Exception as e:
                print(f"[Webhook Sync] Edit error: {e}")

    # ------------------------------------------------------------
    # ❌ DELETE SYNC
    # ------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or message.webhook_id or message.guild is None:
            return

        groups = await self.storage.get("sync_webhooks", "groups", default={})
        if str(message.channel.id) not in groups:
            return

        target_webhooks = groups[str(message.channel.id)]
        for webhook_url in target_webhooks:
            try:
                target_channel_id = int(webhook_url.split("/")[5])
                target_msg_id = self.message_map.get(message.channel.id, {}).get(message.id, {}).get(target_channel_id)
                if not target_msg_id:
                    continue
                webhook = discord.Webhook.from_url(webhook_url, session=self.session)
                await webhook.delete_message(target_msg_id)
            except Exception as e:
                print(f"[Webhook Sync] Delete error: {e}")

    # ------------------------------------------------------------
    # 🧹 CLEANUP
    # ------------------------------------------------------------
    async def cog_unload(self):
        await self.session.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(WebhookChannelSync(bot))
