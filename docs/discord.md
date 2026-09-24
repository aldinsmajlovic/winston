# Discord

Talk to Winston in Discord DMs and get announcements there; `send_message` lets it message people
in `channels.discord.contacts` (name to Discord user id) and you ("me").

1. discord.com/developers/applications: New Application, then Bot: Reset Token (copy it) and turn on
   Message Content Intent, plus Server Members Intent if the Contacts dialog should list people. Under
   Installation, allow user install or invite it to a private server. The bot can only DM people who
   share a server with it.
2. Discord: Settings, Advanced, Developer Mode on; right-click yourself, Copy User ID.
3. Settings, Channels, Discord: paste the token into Bot token (stored as `DISCORD_BOT_TOKEN` in
   `data/.env`, never shown again), pick or paste yourself under Your Discord user id
   (`channels.discord.allowed_user`), switch it on and save, then start discord from the Status page.
   By hand: `DISCORD_BOT_TOKEN=...` in `data/.env` and `allowed_user: "<id>"` under `channels.discord`.

The user id is not a secret, so it lives in config. Older configs that keep it in `data/.env` as
`DISCORD_ALLOWED_USER_ID` still work: while `allowed_user` is empty, the variable that
`allowed_user_env` names is read. Every channel's brain reads the token and your id for
`send_message`, so changing either marks the voice, Discord and satellite channels for a restart.

Contacts are a table (Name, Discord user) with an Add icon, a dialog per contact and trash with Undo.
With the token set, the dialog lists the people in the bot's servers by name, grouped by server, and
picking one fills in the name; a pasted id that is not listed is looked up and shown as the username,
or with a warning icon saying why it was not found. Listing needs Server Members Intent; without it
the warning icon says so. Without a token the user field is a plain text box for a pasted id (its (i)
says where to copy one), and so is Your Discord user id.

The Discord card's title shows the channel's state icon from the Status page; hover it for the
detail, such as a missing token.

If the token or your user id is missing, winstond does not start the channel: `winston status` and
the Status page show discord in error with `DISCORD_BOT_TOKEN empty in data/.env` (or whichever
variables it lacks). Set them and start it again from the Status page or `winston restart discord`.
A token Discord rejects makes the process exit; the status then shows the exit code and the last log
line, and `data/logs/discord.log` has the rest.

Only that one user is answered; DMs from anyone else are ignored and logged. A conversation starts
fresh after `session_idle_mins` of silence.

Settings under `channels.discord` (web UI: Channels). Announcements reach Discord per
`announce.discord`; see [announcements](announcements.md).
