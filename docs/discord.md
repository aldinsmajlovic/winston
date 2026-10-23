# Discord

Talk to Winston in Discord DMs and get announcements there; `send_message` lets it message people
in `channels.discord.contacts` (name to Discord user id) and you ("me").

1. discord.com/developers/applications: New Application, then Bot: Reset Token (copy it) and turn on
   Message Content Intent, plus Server Members Intent so People he can message can list them. Under
   Installation, allow user install or invite it to a private server. The bot can only DM people who
   share a server with it.
2. Discord: Settings, Advanced, Developer Mode on; right-click yourself, Copy User ID.
3. Settings, Ways to talk, Discord: paste the token into Bot token (kept on this computer as
   `DISCORD_BOT_TOKEN` in `data/.env`, never shown again), pick or paste yourself under Your Discord
   user ID (`channels.discord.allowed_user`), switch on Talk to him on Discord and save. Saving starts
   it, and switching it off and saving stops it. By hand: `DISCORD_BOT_TOKEN=...` in `data/.env` and
   `allowed_user: "<id>"` under `channels.discord`.

The user id is not a secret, so it lives in config. Older configs that keep it in `data/.env` as
`DISCORD_ALLOWED_USER_ID` still work: while `allowed_user` is empty, the variable that
`allowed_user_env` names is read, the older fallback, set in config.yaml only. Every channel's brain reads the token and your id for
`send_message`, so changing either marks the voice, Discord and satellite channels for a restart.

People he can message is a table (Person, Discord user) with Add person, a dialog per contact and
trash with Undo. With the token set, the dialog lists the people in the bot's servers by name, grouped
by server, and picking one fills in the name; a pasted id that is not listed is looked up and shown as
the username, or a line under the field says why it was not found. Listing needs Server Members
Intent, and says so without it. Without a token the user field is a plain text box for a pasted id,
with a line asking for the bot token first, and so is Your Discord user ID.

The Discord card's title shows the channel's state in a word beside its icon whenever it is not
running (Failed, Stopped, Starting); tap the icon for the detail, such as a missing token.

If the token or your user id is missing, winstond does not start the channel: `winston status` and
the Status page show Discord as failed with `DISCORD_BOT_TOKEN empty in data/.env` (or whichever
variables it lacks, or `Your Discord user ID is not set: add it on Ways to talk, Discord`), and Status
lists it under Needs you with Log, Restart and Open Discord. Set them and start it
again there or with `winston restart discord`.
A token Discord rejects makes the process exit; the status then shows the exit code and the last log
line, and `data/logs/discord.log` has the rest.

Only that one user is answered; DMs from anyone else are ignored and logged. A conversation starts
fresh after New conversation after (`session_idle_mins`, 30 min) of quiet.

While the brain is stopped or still starting, a message gets _He could not reach his brain. It may be
stopped or still starting._ instead of an answer; any other failure says the log has the details
(`data/logs/discord.log`). The Talk page and `winston talk` say the same, and a box shows its error.

## Saying which room you are in

A DM has no room, so "turn off the lights" does not say whose. Type `/room` in the DM and pick one: the
list is the Talk page's room choices (the boxes' rooms, then Home Assistant's areas), plus none. Your
next message starts a conversation in that [room](rooms.md): each message is stamped with it, so "the
lights" means its lights, and the conversation is the room's, shared with its box and screens. `/room`
alone says which room is set, and `/room none` clears it.

The room clears after Room set with /room lasts (`room_mins`, 60 min) without a message from you, so it
does not follow you out of the house; your next message says so. Setting or clearing a room starts a new
conversation once the one under way has answered. Discord registers the command when the channel
connects; a name not in the list is refused with the list.

Settings under `channels.discord` (Settings, Ways to talk, Discord). Announcements reach Discord per
`announce.discord`; see [announcements](announcements.md).
