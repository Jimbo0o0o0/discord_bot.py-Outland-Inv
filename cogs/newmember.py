from discord.ext import commands
#TODO: Auto role management for new member using secrete password 

class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_member = None

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = member.guild.system_channel
        if channel is not None:
            await channel.send(f"Welcome to the server {member.mention}.")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        channel = member.guild.system_channel
        if channel is not None:
            await channel.send(f"{member.mention} has left the server. Goodbye!")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        channel = guild.system_channel
        if channel is not None:
            await channel.send("Hello! Thanks for inviting me to your server!")
            
    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        # You can log this event or perform cleanup if necessary
        print(f"Removed from guild: {guild.name}")   

async def setup(bot):
    await bot.add_cog(Welcome(bot))
