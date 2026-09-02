
from utils.AsyncJSONStorage import AsyncJSONStorage
from utils.utils import PresenceManager

import asyncio
import logging
import logging.handlers
import os
import platform

import discord
from discord.ext import commands
from discord.ext.commands import Context
from dotenv import load_dotenv

load_dotenv()

# TEST: multi guild
# on_guild_join
# on_member_join
# on_ready
# on_guild_remove
#role management and secret pass roles
#general management commands

DEFAULT_STATUS = "Watching over Inv. Guild"
"""	
Setup bot intents (events restrictions)
For more information about intents, please go to the following websites:
https://discordpy.readthedocs.io/en/latest/intents.html

Default Intents:
intents.bans = True
intents.dm_messages = True
intents.dm_reactions = True
intents.dm_typing = True
intents.emojis = True
intents.emojis_and_stickers = True
intents.guild_messages = True
intents.guild_reactions = True
intents.guild_scheduled_events = True
intents.guild_typing = True
intents.guilds = True
intents.integrations = True
intents.invites = True
intents.messages = True # `message_content` is required to get the content of the messages
intents.reactions = True
intents.typing = True
intents.voice_states = True
intents.webhooks = True

Privileged Intents (Needs to be enabled on developer portal of Discord), please use them only if you need them:

"""
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = False

class LoggingFormatter(logging.Formatter):
    # Colors
    black = "\x1b[30m"
    red = "\x1b[31m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    blue = "\x1b[34m"
    gray = "\x1b[38m"
    # Styles
    reset = "\x1b[0m"
    bold = "\x1b[1m"

    COLORS = {
        logging.DEBUG: gray + bold,
        logging.INFO: blue + bold,
        logging.WARNING: yellow + bold,
        logging.ERROR: red,
        logging.CRITICAL: red + bold,
    }

    def format(self, record):
        log_color = self.COLORS[record.levelno]
        format = "(black){asctime}(reset) (levelcolor){levelname:<8}(reset) (green){name}(reset) {message}"
        format = format.replace("(black)", self.black + self.bold)
        format = format.replace("(reset)", self.reset)
        format = format.replace("(levelcolor)", log_color)
        format = format.replace("(green)", self.green + self.bold)
        formatter = logging.Formatter(format, "%Y-%m-%d %H:%M:%S", style="{")
        return formatter.format(record)

class AsyncLoggingHandler(logging.Handler):
    def __init__(self, handler, loop):
        super().__init__()
        self.handler = handler
        self.queue = asyncio.Queue()
        self.loop = loop
        self._task = self.loop.create_task(self._process_queue())

    def emit(self, record):
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, record)
        except Exception as e:
            print(f"Error in emit: {e}")

    async def _process_queue(self):
        while True:
            record = await self.queue.get()
            try:
                self.handler.emit(record)
                if hasattr(self.handler, 'stream') and hasattr(self.handler.stream, 'flush'):
                    self.handler.stream.flush()
            except Exception as e:
                print(f"Error processing log record: {e}")
                self.handleError(record)
            self.queue.task_done()

    def close(self):
        try:
            self.handler.close()
        except Exception:
            pass
        super().close()

class DiscordBotLogger:
    def __init__(self, base_log_dir="logs"):
        self.base_log_dir = base_log_dir
        self.loggers = {}
        self.levels = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        self.base_logger = None
        self.handlers = []  # Keep strong references to handlers
        os.makedirs(base_log_dir, exist_ok=True)

    async def setup(self, loop):
        self.base_logger = logging.getLogger("discord_bot")
        self.base_logger.setLevel(logging.DEBUG)

        # Console handler with async
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(LoggingFormatter())
        async_console = AsyncLoggingHandler(console_handler, loop)
        self.handlers.append(async_console)

        # Rotating file handler for base logger (direct by default)
        base_file_handler = logging.handlers.RotatingFileHandler(
            filename=os.path.join(self.base_log_dir, "discord_main.log"),
            encoding="utf-8",
            maxBytes=10*1024*1024,
            backupCount=5
        )
        file_handler_formatter = logging.Formatter(
            "[{asctime}] [{levelname:<8}] {name}: {message}",
            "%Y-%m-%d %H:%M:%S",
            style="{"
        )
        base_file_handler.setFormatter(file_handler_formatter)
        # Using direct RotatingFileHandler for base logger to ensure reliable logging to discord.log
        async_file = base_file_handler
        # Optional: Uncomment to use AsyncLoggingHandler for non-blocking logging, if needed
        #async_file = AsyncLoggingHandler(base_file_handler, loop)
        self.handlers.append(async_file)

        self.base_logger.addHandler(async_console)
        self.base_logger.addHandler(async_file)

    def get_guild_logger(self, guild: discord.Guild, loop):
        guild_id = str(guild.id)
        if guild_id not in self.loggers:
            guild_logger = logging.getLogger(f"discord_bot.guild_{guild_id}")
            guild_logger.setLevel(logging.DEBUG)
            guild_logger.propagate = False  # Prevent propagation to parent logger

            guild_file_handler = logging.handlers.RotatingFileHandler(
                filename=os.path.join(self.base_log_dir, f"guild_{guild_id}.log"),
                encoding="utf-8",
                maxBytes=5*1024*1024,
                backupCount=3
            )
            guild_file_handler.setFormatter(logging.Formatter(
                "[{asctime}] [{levelname:<8}] {name}: {message}",
                "%Y-%m-%d %H:%M:%S",
                style="{"
            ))
            # Optional: Uncomment to use AsyncLoggingHandler for non-blocking logging, if needed
            #async_guild_file = AsyncLoggingHandler(guild_file_handler, loop)
            self.handlers.append(guild_file_handler)
            
            guild_logger.addHandler(guild_file_handler)
            guild_logger.addHandler(self.base_logger.handlers[0])
            self.loggers[guild_id] = guild_logger
            
        return self.loggers[guild_id]

class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or(os.getenv("PREFIX") or "!"),
            intents=intents,
            help_command=None,
        )
        """
        This creates custom bot variables so that we can access these variables in cogs more easily.

        For example, The logger is available using the following code:
        - self.logger # In this class
        - bot.logger # In this file
        - self.bot.logger # In cogs
        """
        self.logger = DiscordBotLogger()
        self.bot_prefix = os.getenv("PREFIX") or "!"
        self.invite_link = os.getenv("INVITE_LINK")
        
        self.db = AsyncJSONStorage(filename="data/data.json", save_delay=1.0, backup_count=2)
        self.storage = self.db  # alias — cogs use bot.db
        self.presence_manager = PresenceManager(self, DEFAULT_STATUS)
        
    async def setup_hook(self) -> None:
        await self.logger.setup(self.loop)
        self.db.logger = self.logger
        await self.db.load()

        #self.logger.base_logger.debug("Debug: Bot starting up")
        #self.logger.base_logger.info("Info: Bot starting up")
        #self.logger.base_logger.warning("Warning: Bot starting up")
        self.logger.base_logger.info(f"Logged in as {self.user.name}")
        self.logger.base_logger.info(f"discord.py API version: {discord.__version__}")
        self.logger.base_logger.info(f"Python version: {platform.python_version()}")
        self.logger.base_logger.info(
            f"Running on: {platform.system()} {platform.release()} ({os.name})"
        )
        self.logger.base_logger.info("-------------------")
        await self.load_cogs()

    async def load_cogs(self) -> None:
        for file in os.listdir(f"{os.path.realpath(os.path.dirname(__file__))}/cogs"):
            if file.endswith(".py") and file != "template.py":
                extension = file[:-3]
                try:
                    await self.load_extension(f"cogs.{extension}")
                    self.logger.base_logger.info(f"Loaded extension '{extension}'")
                except Exception as e:
                    exception = f"{type(e).__name__}: {e}"
                    self.logger.base_logger.error(
                        f"Failed to load extension {extension}\n{exception}"
                    )

    async def on_ready(self) -> None:
        self.logger.base_logger.info(f"{self.user} is now online and ready!")
        self.logger.base_logger.info(f"Number of servers I'm in: {len(self.guilds)}")
        await self.presence_manager.force_update()
        
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user or message.author.bot:
            return
        if message.guild:
            guild_logger = self.logger.get_guild_logger(message.guild, self.loop)
            guild_logger.info(
                f"Message from {message.author} ({message.author.id}) in #{message.channel} "
                f"(chars={len(message.content or '')}, attachments={len(message.attachments)})"
            )
        await self.process_commands(message)

    async def on_command_completion(self, context: Context) -> None:
        full_command_name = context.command.qualified_name
        split = full_command_name.split(" ")
        executed_command = str(split[0])
        if context.guild is not None:
            guild_logger = self.logger.get_guild_logger(context.guild, self.loop)
            guild_logger.info(
                f"Executed {executed_command} command by {context.author} (ID: {context.author.id})"
            )
        else:
            self.logger.base_logger.info(
                f"Executed {executed_command} command by {context.author} (ID: {context.author.id}) in DMs"
            )

    async def on_command_error(self, context: Context, error) -> None:
        # Suppress CommandNotFound errors to avoid spamming users
        if isinstance(error, commands.CommandNotFound):
            return

        # Generic logging for all handled errors
        if context.guild:
            guild_logger = self.logger.get_guild_logger(context.guild, self.loop)
            logger = guild_logger
        else:
            logger = self.logger.base_logger

        try:
            if isinstance(error, commands.CommandOnCooldown):
                minutes, seconds = divmod(error.retry_after, 60)
                hours, minutes = divmod(minutes, 60)
                hours = int(hours % 24)
                minutes = int(minutes)
                seconds = int(seconds)

                time_parts = []
                if hours > 0:
                    time_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes > 0:
                    time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
                if seconds > 0 or not time_parts:  # Always show seconds if no other parts
                    time_parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

                time_str = " ".join(time_parts)
                embed = discord.Embed(
                    description=f"**Please slow down!** You can use this command again in {time_str}.",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.info(f"{context.author} (ID: {context.author.id}) hit cooldown for {context.command} in {context.guild.name if context.guild else 'DMs'}")

            elif isinstance(error, commands.NotOwner):
                embed = discord.Embed(
                    description="❌ You are not the owner of the bot!",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute owner-only command '{context.command}' in {context.guild.name if context.guild else 'DMs'}"
                )

            elif isinstance(error, commands.MissingPermissions):
                missing_perms = [perm.replace('guild_permissions.', '').replace('_', ' ').title() for perm in error.missing_permissions]
                embed = discord.Embed(
                    title="❌ Missing Permissions",
                    description=f"You are missing the permission{'s' if len(missing_perms) > 1 else ''}: **{', '.join(missing_perms)}**",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.warning(
                    f"{context.author} (ID: {context.author.id}) missing permissions for '{context.command}' in {context.guild.name if context.guild else 'DMs'}: {', '.join(error.missing_permissions)}"
                )

            elif isinstance(error, commands.BotMissingPermissions):
                missing_perms = [perm.replace('guild_permissions.', '').replace('_', ' ').title() for perm in error.missing_permissions]
                embed = discord.Embed(
                    title="❌ Bot Missing Permissions",
                    description=f"I am missing the permission{'s' if len(missing_perms) > 1 else ''}: **{', '.join(missing_perms)}**",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.warning(
                    f"Bot missing permissions for '{context.command}' in {context.guild.name if context.guild else 'DMs'}: {', '.join(error.missing_permissions)}"
                )

            elif isinstance(error, commands.MissingRequiredArgument):
                embed = discord.Embed(
                    title="❌ Missing Argument",
                    description=f"{str(error).capitalize()}",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.info(
                    f"{context.author} (ID: {context.author.id}) missing required argument for '{context.command}' in {context.guild.name if context.guild else 'DMs'}"
                )

            elif isinstance(error, commands.TooManyArguments):
                embed = discord.Embed(
                    title="❌ Too Many Arguments",
                    description="You've provided too many arguments for this command. Check the command usage.",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.info(
                    f"{context.author} (ID: {context.author.id}) provided too many arguments for '{context.command}' in {context.guild.name if context.guild else 'DMs'}"
                )

            elif isinstance(error, commands.BadArgument):
                # Handles converter errors like ChannelNotFound, RoleNotFound, MemberNotFound, etc.
                arg_str = str(error.original) if hasattr(error, 'original') else str(error)
                embed = discord.Embed(
                    title="❌ Invalid Argument",
                    description=f"The argument `{arg_str}` is invalid. Please check your input.",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.warning(
                    f"{context.author} (ID: {context.author.id}) provided invalid argument '{arg_str}' for '{context.command}' in {context.guild.name if context.guild else 'DMs'}"
                )

            elif isinstance(error, commands.MaxConcurrencyReached):
                embed = discord.Embed(
                    title="❌ Command Limit Reached",
                    description=f"This command is already running in {error.per} ({error.type.value}). Please wait until it finishes.",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.info(
                    f"{context.author} (ID: {context.author.id}) hit max concurrency for '{context.command}' in {context.guild.name if context.guild else 'DMs'}"
                )

            else:
                # For any unhandled errors, log and send a generic message
                embed = discord.Embed(
                    title="❌ An unexpected error occurred",
                    description="Please try again later or contact the bot owner if the issue persists.",
                    color=0xE02B2B,
                )
                await context.send(embed=embed)
                logger.error(
                    f"Unhandled command error for '{context.command}' by {context.author} (ID: {context.author.id}) in {context.guild.name if context.guild else 'DMs'}: {error}",
                    exc_info=error.__traceback__
                )

        except discord.HTTPException as http_error:
            # If sending the error message fails (e.g., no permissions, channel deleted)
            logger.error(
                f"Failed to send error message for command '{context.command}' in {context.guild.name if context.guild else 'DMs'}: {http_error}"
            )
        except Exception as send_error:
            # Catch any other errors during error handling
            logger.error(
                f"Unexpected error while handling command error for '{context.command}' in {context.guild.name if context.guild else 'DMs'}: {send_error}",
                exc_info=send_error.__traceback__
            ) 
            
def main():
    token = os.getenv("TOKEN")
    if not token:
        raise SystemExit(
            "TOKEN is not set. Copy .env.example to .env and add your bot token."
        )
    discord_bot = DiscordBot()
    discord_bot.run(token)

if __name__ == "__main__":
    main()