#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
build_dir="$script_dir/build"

if ! command -v javac >/dev/null || ! command -v d8 >/dev/null; then
  echo "Install a JDK and Android SDK build-tools (d8), then put both on PATH." >&2
  exit 1
fi

rm -rf "$build_dir"
mkdir -p "$build_dir/classes"
javac -source 8 -target 8 -d "$build_dir/classes" "$script_dir/Gesture.java"
d8 --min-api 31 --output "$build_dir" "$build_dir/classes/pogobot/Gesture.class"
install -m 0644 "$build_dir/classes.dex" "$repo_root/pogobot/vendor/gesture.dex"
echo "Built pogobot/vendor/gesture.dex"
