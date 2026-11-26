# Changes

What each release brings, newest first. Winston shows the entries newer than yours on Status before
you update, and `winston update` prints them after.

## 0.1.20 (2026-11-26)

- Claude can be his brain, through Anthropic's own API: pick Claude (Anthropic API) under Runs on and
  give it a key. His instructions are cached between requests, and spoken requests think as little as
  they can, so he answers quickly.
- Downloading, installing and starting a part of Winston shows a notice with its progress on every page,
  and a part that fails or stops answering says why.
- Retraining Okay Winston on your own voice is one script, `services/voice-pe/wake.py`: record takes,
  train, and flash the box.
- A skipped setup step holds a dash in its circle instead of pushing the last step onto a second line.
  Motion is calmer and follows reduce motion, and a save rings each card it changed.

## 0.1.19 (2026-11-26)

- The brain's Model opens a dialog of models to choose by: each with the memory it needs at your context
  and whether it fits, where it comes from, its size, context, license, downloads and last update.
- A brain the computer stopped for want of memory says so, on Talk, in Home Assistant and on its status,
  instead of saying it could not reach his brain while Status said healthy. It no longer reloads in a loop.
- A link to a setting (Turn on recordings, Set your phone) scrolls to it and rings its card for a moment.
- The room picker shows how many of each room's entities are exposed, and Status lists only the parts
  that are on.
- Settings follow what you picked, not what was saved: a hosted brain's Model no longer talks of
  downloads, and lists for a new engine or address no longer show the old one's while they load.
- Setup explains installing on Unraid or Proxmox: in a Debian or Ubuntu virtual machine, and what to
  give it.

## 0.1.18 (2026-11-26)

- Winston has a colour of his own, petrol: on his mark, buttons, switches and the page you are on. Save
  stays grey until there is something to save, and on a phone every button and field is easier to tap.
- Setup shows its picks for your computer in one card, with how much there is to download against the
  free space, and every step after the first has Back. Its last step gives the address to open on a phone.
- Finishing setup now starts the brain when Brain and voice was skipped; before, every request failed
  while Talk said Ready.
- Home Assistant without a token yet is a step under Set up next rather than an error, and Talk no
  longer counts on it until it answers.
- Talk is first in the Settings sidebar. An extension's page says what it can reach right under its
  switch, an action that uses the internet says so, and settings that belong to a switch stay folded
  while it is off.
- Ways to talk has one From Home Assistant card for the integration and Assist, saying which to use,
  with a warning once Assist's open port is on.
- Clearer wording throughout: "his computer" where "this computer" was wrong on a phone, add buttons
  that say what they add, and what he did in the past tense.

## 0.1.17 (2026-11-26)

- The memory bar follows vLLM's share of the card as set in How it runs, as the model search already did,
  instead of always assuming 70%.

## 0.1.16 (2026-11-26)

- The memory bar no longer counts a running vLLM brain twice, once as the brain and again as other
  programs, which made a setup that runs fine read as not fitting.

## 0.1.15 (2026-11-26)

- Restart on Status restarts all of Winston from the page: every part, and the settings that wait for
  his own restart.
- Status's graphics memory says which parts run on the card, hearing included, and leaves out a voice
  on the processor.

## 0.1.14 (2026-11-25)

- The memory bar now also works without an NVIDIA card: on an AMD card it shows the card's memory, and
  on a computer with no graphics card the computer's own, and setup sizes the brain to it.
- On an AMD card the brain can run on llama.cpp too (llama.cpp on AMD). It is new and not yet tried on
  an AMD card; Ollama remains the sure way there.
- A computer with two NVIDIA cards shows its graphics memory again.

## 0.1.13 (2026-11-25)

- A graphics memory bar on the Brain and Voice pages and in the setup wizard shows how full your card
  would be with your picks, in green, amber or red, as you change them.
- How much he keeps in mind is now a slider on the Brain page. Setup gives a card with memory to spare
  longer conversations, and the wizard can take you back to the picks recommended for your card.

## 0.1.12 (2026-11-25)

- Setup picks the brain and voice to fit your graphics card: 8, 12 and 16 GB NVIDIA cards now each get
  their own picks, so a smaller card no longer gets a brain that does not fit, and a 12 or 16 GB card
  speaks with a voice on the card instead of the processor.

## 0.1.11 (2026-11-25)

- Home Assistant's app, Assist chat and voice satellites can talk to Winston through his integration,
  with his reply streamed so a satellite starts speaking sooner. Pick Winston as the conversation agent
  in a voice assistant pipeline. Its satellites also speak his announcements and reminders, as with
  Assist.
- Adding the integration is two buttons on Ways to talk, also offered in the setup wizard: Download in
  Home Assistant opens it in Home Assistant's Marketplace or HACS, and Add to Home Assistant adds him
  there with no code to type. Pairing with a code still works.
- Home Assistant shows the integration's real version, the same as Winston's.

## 0.1.10 (2026-11-25)

- `winston update` no longer stops with an error after installing, and restarts the running service
  again. Updating from 0.1.9 or earlier still shows that error once; the new release is installed
  all the same.

## 0.1.9 (2026-11-25)

- A setting you changed but have not saved says so under it, with the value still in effect ("Not
  saved yet: still 512 tokens"), and the page header counts unsaved changes beside Save.

## 0.1.8 (2026-11-24)

- Searching for a vLLM brain model shows each model's size, and greys out one too big for the
  graphics memory vLLM may use, saying so. Before, it could be picked and the brain failed to start.
- When the brain or another engine fails to start because graphics memory ran out, Status says so and
  which setting to change, instead of "see docker logs".
- Check now on Status scrolls to the Updates card when it finds a release and the card moves to the
  top of the page.

## 0.1.7 (2026-11-24)

- Setup no longer asks to save and restart at every step. Each step saves as you go, and Finish
  restarts whatever needs it.
- Downloads show a bar that moves as they go. A Docker image says when it is unpacking, which can take
  minutes after the download, instead of sitting at "8.7 GB of 8.7 GB".

## 0.1.6 (2026-11-24)

- Check now on Status's Updates card looks for a new release at once.

## 0.1.5 (2026-11-24)

- Searching for a brain model on the Brain page and in setup shows what it finds; it said Nothing
  found before.
- Sign in to Home Assistant checks the address first and says why it cannot use it, instead of
  opening a dead page. The login opens in a new tab and closes itself when done.
- Saving a long list names the first three entries and how many more, not every one.
- On phones, the Talk page's header keeps the same side gap as the microphone row.
- The example personality no longer mentions locks and alarms, which could make a small brain refuse
  "wake me at 7".

## 0.1.4 (2026-11-24)

- Setup no longer asks which setup you want in the terminal: you pick the brain, voice and hearing on
  the setup page. If a pick needs something installed first, like Ollama or Docker, its card says so
  and the one command that installs it.
- Setup, update and uninstall read better in the terminal: grouped, in colour, with what is missing
  marked.

## 0.1.3 (2026-11-24)

- Winston checks for a new release on his own and shows it on Status with what changed, and Update
  installs it and restarts him.
- Each release has notes you can read here, on GitHub and on Status.
- Hearing is now Fluid Speech Pro, a retrained Parakeet that makes far fewer mistakes when other people
  are talking, still on the CPU. Parakeet stays a choice under Hearing.
- One profile for a 24 GB NVIDIA card: the 27B brain with Breeze TTS 2, which setup installs in full. The
  9B on vLLM and Chatterbox stay choices on the Brain and Voice pages.
- The Ollama profile's brain is now Granite 4.2 8B, which got every request right in Winston's trials.

## 0.1.2 (2026-11-24)

- The brain can run on another computer on your network (Ollama, vLLM, llama.cpp or any
  OpenAI-compatible server): pick A server on your network under Runs on. No key is needed unless
  that server asks for one.
- Signing in to Home Assistant from the setup wizard works from another device too.

## 0.1.1 (2026-11-24)

- A Home Assistant integration: each room box's state, whether each part runs, his announcements and
  your extensions' entities show up in Home Assistant.
- The brain's model field searches as you type, on Hugging Face or in Ollama's library.
- The setup wizard is clearer: no key asked for a brain on this computer, plainer download sizes, and
  Finish says where it goes.
- Opening Winston's address while setup is under way goes to the wizard.
- Setting up a Voice PE over USB works on a real box.

## 0.1.0 (2026-11-23)

- The first release: one line installs Winston on Linux, and a setup wizard in the browser picks his
  brain and voice, signs in to Home Assistant, names him and sets up a room box.
