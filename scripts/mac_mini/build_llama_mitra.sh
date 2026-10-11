#!/bin/zsh
# Build the PATCHED llama-server that e12b5 runs: llama.cpp b9830 + llama_swa_cache.patch,
# in ~/llama.cpp-mitra (Homebrew's llama.cpp is left alone as the fallback).
# The patch: upstream re-reads every prompt shorter than Gemma's 1024-token sliding window
# from scratch (docs/mac/LLAMA_CACHE_ISSUE.md §8). Still unfixed on upstream master 2026-10-11.
set -eu
TAG=${1:-b9830}
SRC=$HOME/llama.cpp-mitra
PATCH=${0:A:h}/llama_swa_cache.patch

[[ -d $SRC ]] || git clone --depth 1 --branch $TAG https://github.com/ggml-org/llama.cpp.git $SRC
cd $SRC
git apply --check $PATCH 2>/dev/null && git apply $PATCH || grep -q 'MITRA PATCH' tools/server/server-context.cpp
# The embedded web UI downloads from HF at build time (it failed here); the robot never uses it,
# so stub the files the embedder requires.
mkdir -p tools/ui/dist && cd tools/ui/dist
[[ -f index.html ]] || {
  echo '<!doctype html><title>llama-server (Mitra build)</title><p>No web UI in this build.</p>' > index.html
  echo '<p>loading</p>' > loading.html; echo '{}' > manifest.webmanifest; : > sw.js
  echo '{}' > build.json; echo '{"version":"mitra-stub"}' > version.json
  : > bundle-stub.js; : > bundle-stub.css; : > workbox-stub.js
}
cd $SRC
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF \
      -DLLAMA_BUILD_UI=OFF -DLLAMA_BUILD_HTML=OFF
cmake --build build --target llama-server -j 10
build/bin/llama-server --version
