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

After installing [Docker](https://docker.com):

```
docker compose up -d --build
```

> **Note**: `-d` runs the container in the background. Bot data is stored in `./data` and logs in `./logs`.

## Issues or Questions

Open an issue on this repository.

## Versioning

See [UPDATES.md](UPDATES.md).

## Built With

- [discord.py](https://github.com/Rapptz/discord.py)
- [Python 3.12](https://www.python.org/)

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE.md](LICENSE.md) file for details.
