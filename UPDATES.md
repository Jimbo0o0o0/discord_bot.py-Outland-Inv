# Updates List

Here is the list of all the updates that I made on this bot.

### Version 1.0.2 (27 August 2026)

- Fix channel sync deleting replies / warning in every channel (only synced channels now)
- Fix BossCall DMing and stripping reactions on every message when allowed roles are set
- Align activity-call copy with the real timer (15 min / 1 hour Omni)
- Fix slash `sync` command (discord.py has no `override` argument)
- Fix role resolver (`bot.fetch_role` does not exist)
- Single JSON store, persist `data/` + `logs/` in Docker
- Stop logging full message contents
- Giveaway join lock; pick all entrants when fewer than requested winners
- Help command now lists custom cogs; add `.env.example`

### Version 1.0.1 (10 October 2025)

- Pin `discord.py` version to `2.6.3`

### Version 1.0.0 (1 October 2025)

