# UO Outland Guild Helper

Discord helper bot for the Inv. guild on [UO Outlands](https://uooutlands.com).

## Features

- Boss / mini-boss / other activity call menus (reactions + presence)
- Cross-guild channel sync via webhooks
- Giveaways with join/leave buttons
- Outlands wiki, vendor, and map search shortcuts

## How to set up

Copy [`.env.example`](.env.example) to `.env` and replace the placeholder values:

```
TOKEN=your_discord_bot_token
PREFIX=!
INVITE_LINK=your_bot_invite_url
```

Alternatively, set the same names as system environment variables.

### Privileged intents (Developer Portal)

Enable these under **Bot → Privileged Gateway Intents**:

- Server Members Intent
- Message Content Intent
- Presence Intent is **not** required (leave it disabled)

### Bot permissions (server admin)

When inviting the bot, a server admin must grant these permissions. They are already encoded in the sample `INVITE_LINK` in `.env.example` (`permissions=2684611664`).

| Permission | Why the bot needs it |
| --- | --- |
| View Channels | Read command, call, and synced channels |
| Send Messages | Commands, activity pings, giveaways, welcome messages |
| Embed Links | Boss menus, giveaways, error replies |
| Attach Files | Giveaway transparency export |
| Add Reactions | Activity-call menus |
| Manage Messages | Remove used reactions, delete old menus, drop unsupported replies in synced channels |
| Read Message History | Fetch messages to edit, delete, or clean up |
| Mention Everyone | `@here` on activity calls |
| Manage Webhooks | Cross-guild channel sync |
| Manage Channels | Update channel topics when linking synced channels |
| Use Application Commands | Slash commands |

After the invite, keep the bot's role high enough in **Server Settings → Roles** that it can manage messages and webhooks in those channels. Channel permission overwrites can still block the bot even if the invite succeeded.

Invite URL (replace `YOUR_CLIENT_ID`):

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=2684611664&scope=bot%20applications.commands
```

Granting **Administrator** also covers every permission above, but the list is the minimum the bot actually uses.

## How to start
Use start.bat or 
### The "usual" way

Install dependencies:

```
python -m pip install -r requirements.txt
```

Then start the bot:

```
python bot.py
```

> **Note**: You may need to replace `python` with `py`, `python3`, `python3.11`, etc. depending on what Python versions you have installed.

After the bot is online, run `!sync guild` (bot owner only) so slash commands appear in the server.

### Docker

[Docker](https://docs.docker.com/get-docker/) with Compose V2 (`docker compose`) is required. The compose file **will not start** without a `.env` in the repo root — it is loaded via `env_file`.

1. Copy `.env.example` to `.env` and set a real `TOKEN` (the process exits immediately if it is missing).
2. From the repo root:

```
docker compose up -d --build
```

That builds image `uo-outland-guild-helper` and starts the `discord-bot` service. `restart: unless-stopped` brings it back after a reboot until you stop it yourself.

Bind mounts (created automatically if they do not exist):

| Host | Container | Used for |
| --- | --- | --- |
| `./data` | `/bot/data` | JSON store (`data.json`, giveaways, channel sync) |
| `./logs` | `/bot/logs` | Rotating bot logs |

`.env` is **not** copied into the image (see `.dockerignore`). Compose injects it at runtime. Do not put `TOKEN` in `docker-compose.yml` — that file is tracked by git.

Follow logs to confirm login (needed because `-d` hides startup errors):

```
docker compose logs -f discord-bot
```

You should see `is now online and ready!`. Then run `!sync guild` in Discord (bot owner only).

Useful commands:

```
docker compose logs -f discord-bot   # follow logs
docker compose restart discord-bot   # restart
docker compose down                  # stop
docker compose up -d --build         # rebuild after a code change
```

## Issues or Questions

Open an issue on this repository.

## Built With

- [discord.py](https://github.com/Rapptz/discord.py)
- [Python 3.12](https://www.python.org/)

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE.md](LICENSE.md) file for details.
