#!/bin/zsh
set -euo pipefail
project_dir="${0:A:h:h}"
mode="${1:-run}"
if [[ "${mode}" != "--build-only" ]]; then
  osascript -e 'if application id "com.local.codex-perplexity-adapter" is running then tell application id "com.local.codex-perplexity-adapter" to quit' >/dev/null 2>&1 || true
fi
"${project_dir}/build-macos-app.sh"
stage_path="$(mktemp -d /private/tmp/adapter-run.XXXXXX)"
unzip -oq "${project_dir}/dist/Codex-Perplexity-Adapter-macOS-arm64.zip" -d "${stage_path}"
app_path="${stage_path}/Codex Perplexity Adapter.app"
case "${mode}" in
  --build-only) ;;
  run) open -n "${app_path}" ;;
  --verify) open -n "${app_path}"; osascript -e 'application id "com.local.codex-perplexity-adapter" is running' ;;
  --debug) lldb "${app_path}/Contents/MacOS/CodexPerplexityAdapter" ;;
  --logs|--telemetry) open -n "${app_path}"; tail -F "${HOME}/Library/Logs/Codex Perplexity Adapter.log" ;;
  *) print -u2 'usage: script/build_and_run.sh [run|--build-only|--verify|--debug|--logs|--telemetry]'; exit 2 ;;
esac
