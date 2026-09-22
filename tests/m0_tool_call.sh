#!/usr/bin/env bash
# M0 acceptance (WINSTON_PLAN.md §5): a /v1/chat/completions request with a tool
# schema returns a well-formed tool call. Also checks a plain question does NOT
# trigger a tool, and prints wall-clock latency for both.
set -euo pipefail
BASE=${VLLM_URL:-http://127.0.0.1:8000/v1}
MODEL=${VLLM_MODEL:-$(curl -sf "$BASE/models" | jq -r '.data[0].id')}
SYSTEM='You are Winston, a voice assistant in a home. Use tools to control the home. Reply briefly.'

TOOLS='[{"type":"function","function":{"name":"light_set",
  "description":"Set brightness and colour temperature of a light.",
  "parameters":{"type":"object","properties":{
    "entity_id":{"type":"string","description":"Home Assistant entity id, e.g. light.living_room"},
    "brightness_pct":{"type":"integer","minimum":0,"maximum":100},
    "color_temp":{"type":"string","enum":["warm","neutral","cool"]}},
    "required":["entity_id","brightness_pct"]}}}]'

ask() {  # $1 = user text; sets $RESP (response JSON) and $MS (wall-clock ms)
  local body; body=$(jq -n --arg m "$MODEL" --arg sys "$SYSTEM" --arg u "$1" --argjson tools "$TOOLS" \
    '{model:$m, messages:[{role:"system",content:$sys},{role:"user",content:$u}],
      tools:$tools, tool_choice:"auto", max_tokens:256, temperature:0.7, top_p:0.8,
      chat_template_kwargs:{enable_thinking:false}}')
  local t0; t0=$(date +%s%N)
  RESP=$(curl -sf "$BASE/chat/completions" -H 'Content-Type: application/json' -d "$body")
  MS=$(( ($(date +%s%N) - t0) / 1000000 ))
}

echo "model: $MODEL"
echo "--- 1) tool-needing request"
ask "Put the living room lights at 50% and make them warm."; resp=$RESP
echo "$resp" | jq -c '.choices[0].message | {content, tool_calls}'
name=$(echo "$resp" | jq -r '.choices[0].message.tool_calls[0].function.name // empty')
args=$(echo "$resp" | jq -r '.choices[0].message.tool_calls[0].function.arguments // empty')
finish=$(echo "$resp" | jq -r '.choices[0].finish_reason')
[[ "$name" == "light_set" ]] || { echo "FAIL: no light_set tool call (finish_reason=$finish)"; exit 1; }
echo "$args" | jq -e '.brightness_pct == 50 and (.entity_id|type=="string")' >/dev/null \
  || { echo "FAIL: arguments malformed: $args"; exit 1; }
echo "PASS: $name $args  (finish_reason=$finish, ${MS} ms)"

echo "--- 2) plain question (should answer in text, no tool)"
ask "What is the capital of Sweden?"; resp=$RESP
echo "$resp" | jq -c '.choices[0].message | {content, tool_calls}'
if [[ $(echo "$resp" | jq '.choices[0].message.tool_calls | length') -gt 0 ]]; then
  echo "WARN: model called a tool for a plain question"; else echo "PASS: text answer (${MS} ms)"; fi
