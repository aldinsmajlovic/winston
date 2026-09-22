# voice/tts

## Chatterbox-Turbo server

`chatterbox_server/` is a small FastAPI app exposing OpenAI's `/v1/audio/speech` on port 8880,
returning 24 kHz mono PCM (or WAV). It has its own Python 3.11 venv because Chatterbox pins
torch 2.6. Start it from this directory:

```sh
cd voice/tts/chatterbox_server
.venv/bin/python server.py --voice ../reference/vctk/p232_023.wav --voices-dir ../reference
```

Try another voice without restarting by naming a clip relative to `--voices-dir`, e.g.
`{"voice": "vctk/p226_023"}`; set the default in `config.yaml` under `tts.voice`.

## Reference voices

`reference/vctk/*.wav` are sentence 23 from the CSTR VCTK Corpus (University of Edinburgh),
via Kyutai's `kyutai/tts-voices` mirror, licensed **CC BY 4.0**. Attribution: Yamagishi, J.,
Veaux, C., MacDonald, K. (2019), "CSTR VCTK Corpus: English Multi-speaker Corpus for CSTR
Voice Cloning Toolkit (version 0.92)", https://doi.org/10.7488/ds/2645. Speakers pulled as
British male candidates: p226 (Surrey), p232 (Southern England), p243 (London),
p254 (Surrey, **default**, chosen by Aldin 2026-09-22), p258 (Southern England), p273 (Suffolk).

Anything else dropped into `reference/` (a personal clip, for instance) stays out of git.
Plan M2: clone only from a consented or permissively licensed clip; never an actor.
