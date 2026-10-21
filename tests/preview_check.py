"""The Voice page's previews on the real speech server: an unsaved voice and an unsaved streaming setting
spoken through winstond's /api/speak, timed. Read only: /api/speak plays audio back to the caller and
changes nothing, and this script writes no file and saves no setting.
Needs `winston up` with the speech server started after per-request streaming was added.

    uv run python tests/preview_check.py [--url http://127.0.0.1:8700] [--voice vctk/p226_023]

Without --voice it picks a clip from the voice dropdown's own list that is not the saved voice.
Prints, per request: the voice and streaming winstond says applied (X-Voice, X-Streaming), the time to
the response headers, to the first audio byte and to the end, and the seconds of audio.

Pass: every request answers 200 with audio in the voice asked for, and the streamed sample reports
X-Streaming on and its first sound comes sooner than the same sentence unstreamed. The voice is asked
for twice; the difference is what preparing its clip cost the first time."""
import argparse
import sys
import time
from urllib.parse import quote

import httpx

# The Voice page's sample (web/src/hooks/use-voice-previews.tsx): long enough to pass the first chunk
# and the doubling after it.
SAMPLE = ("The main room lights are on at half brightness, the front door is locked, and tomorrow looks dry "
          "with a light wind from the west, so it's a good day to open the windows for an hour in the morning.")

failures: list[str] = []


def check(ok: bool, what: str):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def speak(c: httpx.Client, label: str, body: dict) -> dict:
    t0 = time.perf_counter()
    first = None
    size = 0
    with c.stream("POST", "/api/speak", json=body) as r:
        t_head = time.perf_counter() - t0
        if r.status_code != 200:
            r.read()
            print(f"{label}: {r.status_code} {r.text[:300]}")
            return {"status": r.status_code, "headers": r.headers}
        for chunk in r.iter_bytes():
            if chunk and first is None:
                first = time.perf_counter() - t0
            size += len(chunk)
    total = time.perf_counter() - t0
    first = total if first is None else first
    rate = int(r.headers.get("x-sample-rate") or 24000)
    audio = size / 2 / rate
    print(f"{label}: voice {r.headers.get('x-voice')} streaming {r.headers.get('x-streaming', '-')} | "
          f"headers {t_head:.2f} s, first byte {first:.2f} s, "
          f"total {total:.2f} s, {audio:.2f} s of audio (rtf {total / audio if audio else float('nan'):.2f})")
    return {"status": 200, "headers": r.headers, "first": first, "total": total, "audio": audio}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8700", help="the running winstond")
    ap.add_argument("--voice", help="a voice other than the saved one (default: picked from the dropdown's list)")
    args = ap.parse_args()

    with httpx.Client(base_url=args.url, timeout=httpx.Timeout(10, read=120)) as c:
        saved = c.get("/api/config").raise_for_status().json().get("tts", {})
        status = c.get("/api/status").raise_for_status().json()
        tts = next((e for e in status["engines"] if e["name"] == "tts"), None)
        print(f"saved voice {saved.get('voice')}, streaming {saved.get('streaming')}")
        check(tts is not None and tts["state"] == "healthy", f"speech engine healthy ({tts and tts['state']})")
        check(tts is not None and not tts["config_changed"], "speech engine runs the saved settings (no restart pending)")

        voice = args.voice
        if not voice:
            choices = c.get("/api/options/voices").raise_for_status().json()["choices"]
            voice = next((ch["value"] for ch in choices if ch.get("group") not in (None, "Your voices", "Built in")
                          and ch["value"] != saved.get("voice") and not ch["value"].startswith("friends/")), None)
        if not voice:
            sys.exit("no voice other than the saved one to try; pass --voice")
        check(voice != saved.get("voice"), f"trying {voice}, not the saved voice")

        hello = {"text": f"Hello, I'm {status.get('assistant', 'Winston')}. This is how I sound.", "voice": voice}
        cold = speak(c, "voice, first time", hello)
        warm = speak(c, "voice, again     ", hello)
        for name, r in (("first", cold), ("again", warm)):
            check(r["status"] == 200 and r["audio"] > 0.5, f"voice {name}: 200 with audio")
            check(r["headers"].get("x-voice") == quote(voice, safe="/._-"), f"voice {name}: X-Voice is {voice}")
        if cold["status"] == 200 and warm["status"] == 200:
            print(f"     clip preparation cost about {cold['first'] - warm['first']:.2f} s on the first request")

        off = speak(c, "sample unstreamed", {"text": SAMPLE, "streaming": {"enabled": False}})
        on = speak(c, "sample streamed  ", {"text": SAMPLE, "streaming": {"enabled": True, "chunk_secs": 2.4}})
        check(off["status"] == 200 and off["headers"].get("x-streaming") == "off", "unstreamed sample: X-Streaming off")
        check(on["status"] == 200 and on["headers"].get("x-streaming") == "on",
              f"streamed sample: X-Streaming on ({on['headers'].get('x-streaming')}; 'ignored' means the "
              f"speech server predates per-request streaming)")
        if off["status"] == 200 and on["status"] == 200:
            check(on["first"] < off["first"],
                  f"streamed first sound {on['first']:.2f} s sooner than unstreamed {off['first']:.2f} s")

    print("\nall passed" if not failures else f"\n{len(failures)} failed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
