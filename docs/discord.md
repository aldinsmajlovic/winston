# Discord

Talk to Winston in Discord DMs and get announcements there; `send_message` lets it message people
in `channels.discord.contacts` (name to user id) and you ("me").

1. discord.com/developers/applications: New Application, then Bot: Reset Token (copy it) and turn on
   Message Content Intent. Under Installation, allow user install or invite it to a private server.
   The bot can only DM people who share a server with it.
2. Discord: Settings, Advanced, Developer Mode on; right-click yourself, Copy User ID.
3. `data/.env`: `DISCORD_BOT_TOKEN=...` and `DISCORD_ALLOWED_USER_ID=...`, then
   `channels.discord.enabled: true` and restart winstond.

Only that one user is answered; DMs from anyone else are ignored and logged. A conversation starts
fresh after `session_idle_mins` of silence.

Settings under `channels.discord` (web UI: Channels). Announcements reach Discord per
`announce.discord`; see [announcements](announcements.md).
