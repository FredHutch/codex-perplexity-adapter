#!/bin/zsh
set -euo pipefail
project_dir="${0:A:h:h}"
app_path="/private/tmp/codex-perplexity-anchor-test3/Codex Perplexity Adapter Anchor Test 3.app"
mode="${1:-run}"
# Address only this candidate; preserve the older running Anchor Test.
if [[ "${mode}" != "--build-only" ]]; then
  osascript -e 'if application id "com.local.codex-perplexity-adapter.anchor-test3" is running then tell application id "com.local.codex-perplexity-adapter.anchor-test3" to quit' >/dev/null 2>&1 || true
fi
"${project_dir}/../../.venv/bin/python" "${project_dir}/research/build_anchor_app.py"
case "${mode}" in
  --build-only) ;;
  run) open -n "${app_path}" ;;
  --verify) open -n "${app_path}"; osascript -e 'application id "com.local.codex-perplexity-adapter.anchor-test3" is running' ;;
  --debug) lldb "${app_path}/Contents/MacOS/CodexPerplexityAdapter" ;;
  --logs|--telemetry) open -n "${app_path}"; tail -F "${HOME}/Library/Logs/Codex Perplexity Adapter Anchor Test 3.log" ;;
  *) print -u2 'usage: script/build_and_run.sh [run|--build-only|--verify|--debug|--logs|--telemetry]'; exit 2 ;;
esac
