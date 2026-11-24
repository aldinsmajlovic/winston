# Changes

What each release brings, newest first. Winston shows the entries newer than yours on Status before
you update, and `winston update` prints them after.

## 0.1.7 (unreleased)

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
