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

Enable these privileged intents in the Discord Developer Portal:

- Server Members Intent
- Message Content Intent
- Presence Intent

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
