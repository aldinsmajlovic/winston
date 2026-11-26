# Discord

Talk to Winston in Discord DMs and get announcements there. His `send_message` tool messages you
("me") and the people listed under People he can message.

## Setting it up

1. On discord.com/developers/applications, make a New Application. Under Bot, Reset Token and copy it,
   and turn on Message Content Intent, plus Server Members Intent so People he can message can list
   people. Under Installation, allow user install or invite the bot to a private server. The bot can
   only DM people who share a server with it.
2. In Discord, turn on Settings, Advanced, Developer Mode, then right-click yourself and Copy User ID.
3. In Winston, open Settings, Ways to talk, Discord. Paste the token into Bot token, pick or paste
   yourself under Your Discord user ID, switch on Talk to him on Discord and save.

Saving starts the channel, and switching it off and saving stops it. By hand, put
`DISCORD_BOT_TOKEN=...` in `data/.env` and `allowed_user: "<id>"` under `channels.discord`.

## Settings

Under `channels.discord`, on Settings, Ways to talk, Discord:

| Setting | Key | Default | What it does |
| --- | --- | --- | --- |
| Talk to him on Discord | `enabled` | off | Runs the channel |
| Bot token | `token_env` names the variable | `DISCORD_BOT_TOKEN` | Kept in `data/.env`, never shown again |
| Your Discord user ID | `allowed_user` | empty | The one user he answers |
| New conversation after | `session_idle_mins` | 30 min | Quiet after which a DM starts a fresh conversation |
| Room set with /room lasts | `room_mins` | 60 min | Quiet after which the [room](#saying-which-room-you-are-in) clears |
| People he can message | `contacts` | empty | Names and Discord user ids for `send_message` |

- **Your user id** is not a secret, so it lives in config. Older configs that keep it in `data/.env`
  as `DISCORD_ALLOWED_USER_ID` still work: while `allowed_user` is empty, the variable that
  `allowed_user_env` names is read. That fallback is set in config.yaml only.
- **Every channel's brain** reads the token and your id for `send_message`, so changing either marks
  the voice, Discord and satellite channels for a restart.
- **Announcements** reach Discord as Also send to Discord says ([announcements](announcements.md#settings)).

### People he can message

A table (Person, Discord user) with Add a person, a dialog per contact and trash with Undo.

- **With the token set**, the dialog lists the people in the bot's servers by name, grouped by server,
  and picking one fills in the name. Listing needs Server Members Intent, and says so without it.
- **A pasted id** that is not listed is looked up and shown as the username, or a line under the field
  says why it was not found.
- **Without a token**, the user field is a plain text box for a pasted id, with a line asking for the
  bot token first. So is Your Discord user ID.

## Talking to him

Only your user is answered; DMs from anyone else are ignored and logged. A conversation starts fresh
after New conversation after of quiet.

While the brain is stopped or still starting, a message gets _He could not reach his brain. It may be
stopped or still starting._ instead of an answer, and when this computer ran out of memory for it, _His
brain stopped because the computer ran out of memory_ with what to change
([running](running.md#status-and-errors)). Any other failure says the log has the details
(`data/logs/discord.log`). The Talk page and `winston talk` say the same, and a box shows its error.

## Saying which room you are in

A DM has no room, so "turn off the lights" does not say whose. Type `/room` in the DM and pick one: the
list is the Talk page's room choices (the boxes' rooms, then Home Assistant's areas), plus none.

- Your next message starts a conversation in that [room](rooms.md). Each message is stamped with it,
  so "the lights" means its lights, and the conversation is the room's, shared with its box and screens.
- `/room` alone says which room is set, and `/room none` clears it.
- The room clears after Room set with /room lasts without a message from you, so it does not follow
  you out of the house; your next message says so.
- Setting or clearing a room starts a new conversation once the one under way has answered.
- Discord registers the command when the channel connects. A name not in the list is refused with the
  list.

## When it does not start

The Discord card's title shows the channel's state in a word beside its icon whenever it is not
running (Failed, Stopped, Starting); tap the icon for the detail.

| Cause | What Status and `winston status` say |
| --- | --- |
| No token | `DISCORD_BOT_TOKEN empty in data/.env`, or whichever variables it lacks |
| No user id | `Your Discord user ID is not set: add it on Ways to talk, Discord` |
| A token Discord rejects | The process exits; the status shows the exit code and the last log line |

With the token or your id missing, winstond does not start the channel, and Status lists it under
Needs you with Log, Restart and Open Discord. Set them and start it again there or with
`winston restart discord`. The whole log is `data/logs/discord.log`.
