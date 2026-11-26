"""Finding a brain model by typing (api/options.py, llm_models with `q`): the Hub searched for the engine's
format, a GGUF repo offered at its 4-bit size, every size of a repo named exactly or pasted as a link and of
the one picked now, Ollama's library, what the model picker shows of each (facts), and nothing at all while
offline. Fakes for the Hub, Ollama's search page and its registry; nothing reaches the network.

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
                                "Small-BF16-00001-of-00002.gguf": 8 * GB, "Small-BF16-00002-of-00002.gguf": 8 * GB},
         # vLLM's: shards counted once, a consolidated copy and a subfolder's files left out
         "Org/Big": {"model-00001-of-00002.safetensors": 8 * GB, "model-00002-of-00002.safetensors": 8 * GB,
                     "consolidated.safetensors": 16 * GB, "original/model.safetensors": 16 * GB, "README.md": 1},
         "Org/Small-FP8": {"model.safetensors": 8 * GB}}
SHARE = 0.7 * 24576 * 2 ** 20                       # vLLM's 70% of a 24 GB card
seen: list[dict] = []


async def models(request):
    seen.append(dict(request.query))
    gguf = request.query.get("filter") == "gguf"
    rows = ([{"id": "unsloth/Small-GGUF", "downloads": 2_100_000, "tags": [],
              "siblings": [{"rfilename": f} for f in FILES["unsloth/Small-GGUF"]]},
             {"id": "x/Odd-GGUF", "downloads": 12, "tags": [], "siblings": [{"rfilename": "Odd-IQ3_M.gguf"}]},
             {"id": "x/No-Quants", "downloads": 5, "tags": [], "siblings": [{"rfilename": "README.md"}]}]
            if gguf else
            [{"id": "Org/Small", "downloads": 41_500, "tags": []}, {"id": "y/Small-MLX-4bit", "tags": ["mlx"]},
             {"id": "Org/Big", "downloads": 900, "tags": []}, {"id": "Org/Small-FP8", "downloads": 12, "tags": []}])
    return web.json_response(rows)


async def model(request):
    repo = f"{request.match_info['owner']}/{request.match_info['name']}"
    if repo not in FILES:
        return web.json_response({"error": "not found"}, status=404)
    card = {"cardData": {"base_model": ["Org/Small"], "license": "apache-2.0", "tags": ["vision"]}, "likes": 7,
            "downloads": 2_100_000, "lastModified": "2026-11-12T02:15:52.000Z",
            "gguf": {"total": 27_320_697_856, "context_length": 262144}} if repo == "unsloth/Small-GGUF" else {}
    return web.json_response({**card, "siblings": [{"rfilename": f, "size": n} for f, n in FILES[repo].items()]})


# One result as ollama.com's search page draws it (2026-11-26), then bare links.
RESULT = """<li class="border-t"> <a href="/library/small3" class="group grid"> <h2 title="small3"><span >small3</span></h2>
 <span class="flex-none text-[15px]" title="Updated August 27, 2026">3mo</span> </div>
 <div class="order-last"><span class="inline-flex items-center rounded-md bg-indigo-50 px-2">tools</span> </div>
 <p class="mt-1 line-clamp-2 text-[15px]">Small models that use tools &amp; think. </p>
 <span class="inline-flex items-center gap-1.5 tabular-nums" title="334,165 downloads"><span >334.2K</span></span>
 <span class="inline-flex items-center rounded-md bg-[#ddf4ff] px-2">3b</span>
 <span  class="inline-flex items-center rounded-md bg-[#ddf4ff] px-2">8b</span
> </a> </li>"""


async def ollama(request):
    return web.Response(text=RESULT + '<a href="/library/small3">y</a><a href="/library/smaller">', content_type="text/html")


async def registry(request):
    if request.match_info["name"] != "small3":
        return web.json_response({"errors": []}, status=404)
    return web.json_response({"layers": [{"size": 5 * GB}], "config": {"size": 1}})


def rows(found):
    return [(c.group, c.value, c.hint) for c in found]


async def main():
    app = web.Application()
    app.router.add_get("/api/models", models)
    app.router.add_get("/api/models/{owner}/{name}", model)
    app.router.add_get("/ollama", ollama)
    app.router.add_get("/v2/library/{name}/manifests/{tag}", registry)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    options.OLLAMA_SEARCH = f"http://127.0.0.1:{port}/ollama"
    options.OLLAMA_REGISTRY = f"127.0.0.1:{port}"

    check("llama.cpp: each GGUF repo at its 4-bit size, the odd one at what it has, one with no GGUF left out",
          rows(await options._find("llamacpp", "small", "")),
          [("Hugging Face", "unsloth/Small-GGUF:Q4_K_M", "2.1M downloads, 3 sizes"),
           ("Hugging Face", "x/Odd-GGUF:IQ3_M", "12 downloads, 1 size")])
    check("the search asks for every typed word and GGUF only", (seen[-1]["search"], seen[-1]["filter"]),
          ("small", "gguf"))
    check("Ollama: its library first, each name once, then the Hub's repos as hf.co/ names",
          [v for _, v, _ in rows(await options._find("ollama", "small", ""))],
          ["small3", "smaller", "hf.co/unsloth/Small-GGUF:Q4_K_M", "hf.co/x/Odd-GGUF:IQ3_M"])
    check("vLLM: safetensors repos with their weights' size, Apple's MLX left out, no size when the Hub has none",
          rows(await options._find("vllm", "small", "")),
          [("Hugging Face", "Org/Small", "41k downloads"), ("Hugging Face", "Org/Big", "17.2 GB, 900 downloads"),
           ("Hugging Face", "Org/Small-FP8", "8.6 GB, 12 downloads")])
    found = {c.value: c for c in await options._find("vllm", "small", "", SHARE)}
    check("vLLM on a card: one too big for its share with a context is greyed and says so",
          (found["Org/Big"].disabled, found["Org/Big"].hint),
          (True, "17.2 GB, too big for the 18.0 GB of graphics memory vLLM may use, with its context"))
    check("one that fits, or of no known size, can be picked",
          (found["Org/Small-FP8"].disabled, found["Org/Small"].disabled), (False, False))
    check("a repo named exactly is sized and greyed the same",
          [(c.value, c.disabled) for c in await options._find("vllm", "Org/Big", "", SHARE)], [("Org/Big", True)])
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

    found = {c.value: c.facts for c in await options._find("llamacpp", "small", "")}
    f = found["unsloth/Small-GGUF:Q4_K_M"]
    check("a Hub find: where from, what it is made from, parameters, its 4-bit size, context, license, counts, tags",
          (f.source, f.link, f.description, f.params, f.bytes, f.context, f.license, f.downloads, f.likes, f.updated,
           f.tags, f.sizes, f.repo),
          ("Hugging Face", "https://huggingface.co/unsloth/Small-GGUF", "Made from Org/Small", "27.3B", 5 * GB, 262144,
           "apache-2.0", 2_100_000, 7, "2026-11-12T02:15:52.000Z", ["vision"], ["BF16", "Q4_K_M", "Q8_0"],
           "unsloth/Small-GGUF"))
    check("a repo the Hub does not answer for keeps what the search said", (found["x/Odd-GGUF:IQ3_M"].downloads,
          found["x/Odd-GGUF:IQ3_M"].bytes), (12, None))
    check("each size of a repo has its own bytes",
          [c.facts.bytes for c in await options._find("llamacpp", "unsloth/Small-GGUF", "")], [5 * GB, 9 * GB, 16 * GB])
    lib = {c.value: c.facts for c in await options._find("ollama", "small", "")}
    check("Ollama's library: its description, pulls, last update, what it does, sizes, and its latest's size",
          (lib["small3"].description, lib["small3"].downloads, lib["small3"].updated, lib["small3"].tags,
           lib["small3"].sizes, lib["small3"].bytes, lib["small3"].link),
          ("Small models that use tools & think.", 334165, "2026-08-27", ["tools"], ["3b", "8b"], 5 * GB + 1,
           "https://ollama.com/library/small3"))
    check("a bare link: its name and page, nothing else known",
          (lib["smaller"].source, lib["smaller"].description, lib["smaller"].bytes), ("Ollama", None, None))
    check("a model listed by name (downloaded, running): the Hub's facts, the disk's size kept",
          ((f := await options._facts("llamacpp", "unsloth/Small-GGUF:Q8_0", 123)).params, f.bytes), ("27.3B", 123))
    check("an Ollama name links its library page, a hosted name has no facts",
          ((await options._facts("ollama", "granite4.2:8b")).link, await options._facts("vllm", "gpt-4o")),
          ("https://ollama.com/library/granite4.2", None))
    cs = [options.Choice(value="granite4.2:8b", label="", facts=options.ModelFacts(bytes=1)),
          options.Choice(value="x/Y:Q4_K_M", label="", searched=True, facts=options.ModelFacts(bytes=4 * GB))]
    await options._add_facts("ollama", cs, {"granite4.2:8b": {"size": 5 * GB, "details": {"parameter_size": "8.2B"}}})
    check("memory by context: measured for a known brain, estimated from the weights for another",
          [(c.facts.measured, c.facts.points[0][0]) for c in cs], [(True, 16), (False, 16)])
    check("Ollama's own list fills in parameters", cs[0].facts.params, "8.2B")

    await runner.cleanup()
    os.environ["HF_ENDPOINT"] = f"http://127.0.0.1:{free_port()}"
    options.OLLAMA_SEARCH = f"http://127.0.0.1:{free_port()}/ollama"
    options.OLLAMA_REGISTRY = f"127.0.0.1:{free_port()}"
    from huggingface_hub import constants
    constants.ENDPOINT = os.environ["HF_ENDPOINT"]
    from winston.engines.install import _FILES
    _FILES.clear()                                  # what the Hub said before is kept for the process
    check("offline: nothing found, no error, so a typed name still works",
          (await options._find("llamacpp", "small", ""), await options._find("ollama", "small", ""),
           await options._find("llamacpp", "", "unsloth/Small-GGUF:Q4_K_M")), ([], [], []))


asyncio.run(main())
finish_checks("model search: the Hub per engine, sizes per repo, links, Ollama's library, facts, offline")
