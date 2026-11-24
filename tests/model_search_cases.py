"""Finding a brain model by typing (api/options.py, llm_models with `q`): the Hub searched for the engine's
format, a GGUF repo offered at its 4-bit size, every size of a repo named exactly or pasted as a link and of
the one picked now, Ollama's library, and nothing at all while offline. Fakes for the Hub and Ollama's search
page; nothing reaches the network.

    uv run python tests/model_search_cases.py
"""
import asyncio
import os

from harness import check, finish_checks, free_port

port = free_port()
os.environ["HF_ENDPOINT"] = f"http://127.0.0.1:{port}"     # read when huggingface_hub is imported

from aiohttp import web  # noqa: E402

from winston.api import options  # noqa: E402

GB = 1024 ** 3
FILES = {"unsloth/Small-GGUF": {"Small-Q8_0.gguf": 9 * GB, "Small-Q4_K_M.gguf": 5 * GB, "mmproj-F16.gguf": GB,
                                "Small-BF16-00001-of-00002.gguf": 8 * GB, "Small-BF16-00002-of-00002.gguf": 8 * GB}}
seen: list[dict] = []


async def models(request):
    seen.append(dict(request.query))
    gguf = request.query.get("filter") == "gguf"
    rows = ([{"id": "unsloth/Small-GGUF", "downloads": 2_100_000, "tags": [],
              "siblings": [{"rfilename": f} for f in FILES["unsloth/Small-GGUF"]]},
             {"id": "x/Odd-GGUF", "downloads": 12, "tags": [], "siblings": [{"rfilename": "Odd-IQ3_M.gguf"}]},
             {"id": "x/No-Quants", "downloads": 5, "tags": [], "siblings": [{"rfilename": "README.md"}]}]
            if gguf else
            [{"id": "Org/Small", "downloads": 41_500, "tags": []}, {"id": "y/Small-MLX-4bit", "tags": ["mlx"]}])
    return web.json_response(rows)


async def model(request):
    repo = f"{request.match_info['owner']}/{request.match_info['name']}"
    if repo not in FILES:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"siblings": [{"rfilename": f, "size": n} for f, n in FILES[repo].items()]})


async def ollama(request):
    return web.Response(text='<a href="/library/small3">x</a><a href="/library/small3">y</a><a href="/library/smaller">',
                        content_type="text/html")


def rows(found):
    return [(c.group, c.value, c.hint) for c in found]


async def main():
    app = web.Application()
    app.router.add_get("/api/models", models)
    app.router.add_get("/api/models/{owner}/{name}", model)
    app.router.add_get("/ollama", ollama)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    options.OLLAMA_SEARCH = f"http://127.0.0.1:{port}/ollama"

    check("llama.cpp: each GGUF repo at its 4-bit size, the odd one at what it has, one with no GGUF left out",
          rows(await options._find("llamacpp", "small", "")),
          [("Hugging Face", "unsloth/Small-GGUF:Q4_K_M", "2.1M downloads, 3 sizes"),
           ("Hugging Face", "x/Odd-GGUF:IQ3_M", "12 downloads, 1 size")])
    check("the search asks for every typed word and GGUF only", (seen[-1]["search"], seen[-1]["filter"]),
          ("small", "gguf"))
    check("Ollama: its library first, each name once, then the Hub's repos as hf.co/ names",
          [v for _, v, _ in rows(await options._find("ollama", "small", ""))],
          ["small3", "smaller", "hf.co/unsloth/Small-GGUF:Q4_K_M", "hf.co/x/Odd-GGUF:IQ3_M"])
    check("vLLM: safetensors repos, Apple's MLX left out", rows(await options._find("vllm", "small", "")),
          [("Hugging Face", "Org/Small", "41k downloads")])
    sizes = [("Sizes of unsloth/Small-GGUF", "unsloth/Small-GGUF:Q4_K_M", "5.4 GB"),
             ("Sizes of unsloth/Small-GGUF", "unsloth/Small-GGUF:Q8_0", "9.7 GB"),
             ("Sizes of unsloth/Small-GGUF", "unsloth/Small-GGUF:BF16", "17.2 GB")]
    check("a repo named exactly: every size, smallest first, split files as one, no vision projector",
          rows(await options._find("llamacpp", "unsloth/Small-GGUF", "")), sizes)
    check("a pasted Hub link reads as the repo",
          rows(await options._find("llamacpp", "https://huggingface.co/unsloth/Small-GGUF/tree/main", "")), sizes)
    check("a pasted Ollama link reads as its name",
          [v for _, v, _ in rows(await options._find("ollama", "https://ollama.com/library/small3", ""))], ["small3"])
    check("nothing typed: the picked model's other sizes, for Ollama as hf.co/ names",
          [v for _, v, _ in rows(await options._find("ollama", "", "hf.co/unsloth/Small-GGUF:Q4_K_M"))],
          ["hf.co/unsloth/Small-GGUF:Q8_0", "hf.co/unsloth/Small-GGUF:BF16"])
    check("one letter searches nothing", rows(await options._find("llamacpp", "s", "")), [])
    check("found choices are shown as the server found them", all(c.searched for c in await options._find(
        "llamacpp", "small", "")), True)

    await runner.cleanup()
    os.environ["HF_ENDPOINT"] = f"http://127.0.0.1:{free_port()}"
    options.OLLAMA_SEARCH = f"http://127.0.0.1:{free_port()}/ollama"
    from huggingface_hub import constants
    constants.ENDPOINT = os.environ["HF_ENDPOINT"]
    from winston.engines.install import _FILES
    _FILES.clear()                                  # what the Hub said before is kept for the process
    check("offline: nothing found, no error, so a typed name still works",
          (await options._find("llamacpp", "small", ""), await options._find("ollama", "small", ""),
           await options._find("llamacpp", "", "unsloth/Small-GGUF:Q4_K_M")), ([], [], []))


asyncio.run(main())
finish_checks("model search: the Hub per engine, sizes per repo, links, Ollama's library, offline")
