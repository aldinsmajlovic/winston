# Tools

Besides Home Assistant's own tools (over its MCP server), Winston has these. All are logged to
`data/logs/tool_calls.jsonl`; settings are in the web UI under Tools.

| Tool | What it does | Needs | Limits |
| --- | --- | --- | --- |
| `get_sensor_readings` | Watched sensors with the age of each reading | `tools.sensors` | Read only |
| `remember` | Stores one fact in `data/memory/*.md`, committed to git there | Nothing | 300 characters; secrets refused |
| `web_search` | Current facts via Gemini with Google Search grounding | `GEMINI_API_KEY`, billing on the key | `tools.web_search.max_per_day` (100) |
| `ask_claude` | Hard questions to Claude through the Agent SDK | A Claude Code login, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | `tools.ask_claude.max_usd_per_day` (3 USD) |
| `send_message` | A Discord DM to a listed contact or you | `DISCORD_BOT_TOKEN`, `channels.discord.contacts` | Listed names only |
| `clone_voice` | Records a friend who agrees out loud and speaks in their voice ([voices](voices.md#cloned-voices)) | The voice channel | Spoken consent, checked; typed channels only say to ask by voice |
| `use_voice` | Switches to a cloned voice or back to his own, optionally kept in config | The voice channel | Cloned voices only |

Only the question text leaves the house for `web_search` and `ask_claude`: no home state, no memory.
Answers come back marked as information, never instructions.

## Region

`region` in config (the Assistant page) sets the units and formats he speaks in: temperature, metric
or imperial, the clock, dates, and the home's currency. Each defaults to "same as Home Assistant",
read from its unit system, country and currency. It goes into the system prompt when a conversation
starts, and he converts what tools and web searches return. Prices in another currency are said as
they are, never with a guessed exchange rate.

## Memory

Facts are `- ` lines in `data/memory/preferences.md`, `people.md` and `projects.md` (any `.md` there is
a topic). They load into the system prompt when a conversation starts, so a new session knows them
without a restart. Relative dates are made absolute when stored ("tomorrow evening" becomes "on
Thursday 24 September 2026 evening") and each fact carries the date it was noted. `winston memory`
prints what the model sees; edit the files to change it.

## Web search

`gemini-3.5-flash-lite` at minimal thinking answers in about 1.4 s at the search model (Flash: 2.3 s,
same answers in our test). Grounding needs billing enabled on the key; Google's paid tier includes a
monthly grounding quota and does not train on the prompts. Over voice Winston says "Let me look that
up." while it runs.

## ask_claude

Runs the bundled Claude Code CLI with no tools, no settings and an empty working directory, one turn.
On a Claude Pro or Max plan it draws on the plan's monthly Agent SDK credit (opt in once in your Claude
account); `claude setup-token` makes a token for a machine without a browser login. Claude Opus 5 at
low effort: about 6 s and 0.05 USD a question. Over voice Winston says "Let me think about that."
