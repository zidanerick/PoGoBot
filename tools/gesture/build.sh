#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "$script_dir/../.." && pwd)"
build_dir="$script_dir/build"

if ! command -v javac >/dev/null || ! command -v d8 >/dev/null || \
   ! command -v python3 >/dev/null; then
  echo "Install a JDK, Android SDK build-tools (d8), and Python 3, then put them on PATH." >&2
  exit 1
fi

rm -rf "$build_dir"
mkdir -p "$build_dir/classes"
javac -source 8 -target 8 -d "$build_dir/classes" "$script_dir/Gesture.java"
mapfile -d '' class_files < <(find "$build_dir/classes" -type f -name '*.class' -print0 | sort -z)
if ((${#class_files[@]} == 0)); then
  echo "javac produced no class files" >&2
  exit 1
fi
d8 --min-api 31 --output "$build_dir" "${class_files[@]}"
python3 "$script_dir/check_dex.py" "$build_dir/classes.dex"
install -m 0644 "$build_dir/classes.dex" "$repo_root/pogobot/vendor/gesture.dex"
echo "Built pogobot/vendor/gesture.dex"
