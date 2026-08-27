# UO Outland Guild Helper

## Support

## Disclaimer

## How to set up

To set up the token you will have to make use of the [`.env.example`](.env.example) file; you should rename it to `.env` and replace the `YOUR_BOT...` content with your actual values that match for your bot.

Alternatively you can simply create a system environment variable with the same names and their respective value.

## How to start

### The _"usual"_ way

To start the bot you simply need to launch, either your terminal (Linux, Mac & Windows), or your Command Prompt (
Windows)
.

Before running the bot you will need to install all the requirements with this command:

```
python -m pip install -r requirements.txt
```

After that you can start it with

```
python bot.py
```

> **Note**: You may need to replace `python` with `py`, `python3`, `python3.11`, etc. depending on what Python versions you have installed on the machine.

### Docker

Support to start the bot in a Docker container has been added. After having [Docker](https://docker.com) installed on your machine, you can simply execute:

```
docker compose up -d --build
```
> **Note**: `-d` will make the container run in detached mode, so in the background.

## Issues or Questions

## Versioning

## Built With
-  [Discord.py]  (https://github.com/Rapptz/discord.py)
- [Python 3.12.9](https://www.python.org/)

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE.md](LICENSE.md) file for details
