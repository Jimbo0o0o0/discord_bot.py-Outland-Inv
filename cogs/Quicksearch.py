from discord.ext import commands
from urllib.parse import quote_plus, quote

class GoogleSearch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name='google', help='Searches Google with the given text and returns the search URL.')
    async def google(self, ctx, query: str):
        search_url = f"https://www.google.com/search?q={quote_plus(query)}"
        await ctx.send(f"Here's your Google search for **{query}**:\n{search_url}")

    @commands.hybrid_command(name='wiki', help='Searches Outland wiki with the given text and returns the search URL.')
    async def wiki(self, ctx, query: str):
        search_url = f"https://wiki.uooutlands.com/index.php?search={quote_plus(query)}&title=Special%3ASearch&go=Go"
        await ctx.send(f"Here's your Outland wiki search for **{query}**:\n{search_url}")

    @commands.hybrid_command(name='price', help='Searches Vendor portal with the given text and returns the search URL.')
    async def price(self, ctx, query: str):
        encoded_query = quote(query, safe="")
        search_url = f"https://portal.uooutlands.com/vendor-search?searchTerm=%22{encoded_query}%22&sortActive=Price&sortDirection=asc"
        await ctx.send(f"Here's your Vendor portal search for **{query}**:\n{search_url}")

    @commands.command(name="map", description="Generate a map URL from a location string. The location string in the format '#uooutlands|location|Current Location|x|y|z'")
    async def map_command(self, ctx, location_string: str):
        parts = location_string.split('|')
        if len(parts) < 6:  # Need at least 6 parts for the example format
            await ctx.send("Invalid location string format. Expected format like '#uooutlands|location|Current Location|x|y|z'")
            return
        
        try:
            # Extract the last three digit fields: x, y, z (but use fixed zoom 10 for URL)
            x = parts[-3].strip()  # e.g., '3675'
            y = parts[-2].strip()  # e.g., '3664'
            # z from string is ignored; URL uses 10 as zoom
            
            # Validate they are digits
            if not (x.isdigit() and y.isdigit()):
                raise ValueError("Coordinates must be numeric.")
            
            url = f"https://exploreoutlands.com/#pos:{x},{y},10"
            await ctx.send(f"Map URL: {url}")
        except ValueError as e:
            await ctx.send(f"Error parsing coordinates: {str(e)}")

async def setup(bot):
    await bot.add_cog(GoogleSearch(bot))