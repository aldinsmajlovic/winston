# Changes

What each release brings, newest first. Winston shows the entries newer than yours on Status before
you update, and `winston update` prints them after.

## 0.1.3 (unreleased)

- Winston checks for a new release on his own and shows it on Status with what changed, and Update
  installs it and restarts him.
- Each release has notes you can read here, on GitHub and on Status.
- Hearing is now Fluid Speech Pro, a retrained Parakeet that makes far fewer mistakes when other people
  are talking, still on the CPU. Parakeet stays a choice under Hearing.
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
