#!/usr/bin/env bash
# Switch the brain's LLM server: ./scripts/llm_switch.sh gemma|qwen
#   gemma  Gemma 4 12B on the Mac Mini (e12b5)
#   qwen   Qwen3.5-9B on the laptop's RTX 4050 (~/llama.cpp-bin/qwen9b.sh; twin sim off)
# Writes LANGROBO_LLM in .env (services/llm_profiles.py), checks the server
# answers, and restarts the brain (asks for the sudo password).
set -euo pipefail
cd "$(dirname "$0")/.."
want="${1:-}"
case "$want" in gemma|qwen) ;; *) echo "usage: $0 gemma|qwen"; exit 2 ;; esac
if grep -q '^LANGROBO_LLM=' .env; then
    sed -i "s/^LANGROBO_LLM=.*/LANGROBO_LLM=$want/" .env
else
    printf '\n# LLM server: gemma = Mac Mini, qwen = laptop (scripts/llm_switch.sh)\nLANGROBO_LLM=%s\n' "$want" >> .env
fi
root=$(cd src/langrobo_core && LANGROBO_LLM="$want" python3 -m langrobo_core.services.llm_profiles root)
if curl -s -m4 "$root/health" | grep -q ok; then
    echo "LLM: $want at $root -- answering"
else
    echo "LLM: $want at $root -- NOT answering yet. Start it: $(cd src/langrobo_core && LANGROBO_LLM="$want" python3 -m langrobo_core.services.llm_profiles start)"
fi
sudo systemctl restart langrobo-brain && echo "brain restarted on $want"
