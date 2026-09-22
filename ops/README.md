# ops

## vLLM (local brain)

```sh
docker compose -f ops/docker-compose.yml up -d vllm      # start
docker compose -f ops/docker-compose.yml logs -f vllm    # watch startup
docker compose -f ops/docker-compose.yml down            # stop and free the GPU
curl -s localhost:8000/v1/models | jq                     # is it up?
```

Weights are pulled once with `hf download QuantTrio/Qwen3.5-9B-AWQ` into `~/.cache/huggingface`, which is mounted into the container. The container runs offline.

`--gpu-memory-utilization 0.70` caps vLLM at ~17 GB of the 3090. The QuantTrio AWQ build keeps attention, layer 0 and the vision tower in BF16, so weights alone are ~12 GB; the rest is KV cache. Raise it on a dedicated box, or lower it once TTS needs the room.

Acceptance test for M0: `tests/m0_tool_call.sh`.
