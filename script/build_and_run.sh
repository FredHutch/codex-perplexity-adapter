#!/bin/zsh
set -euo pipefail
project_dir="${0:A:h:h}"
app_path="${project_dir}/build/qualification-0.1.15/extracted/arm64/Codex Perplexity Adapter Test.app"
mode="${1:-run}"
# Address only the isolated candidate by bundle ID. Never kill the stable app.
osascript -e 'if application id "com.local.codex-perplexity-adapter.resize-test" is running then tell application id "com.local.codex-perplexity-adapter.resize-test" to quit' >/dev/null 2>&1 || true
ADAPTER_PYINSTALLER="${project_dir}/build/resize-build-arm64/bin/pyinstaller" ADAPTER_OUTPUT_DIR="${project_dir}/dist/resize-0.1.15-rc1" "${project_dir}/build-macos-app.sh"
unzip -oq "${project_dir}/dist/resize-0.1.15-rc1/Codex-Perplexity-Adapter-Test-0.1.15-rc1-macOS-arm64.zip" -d "${app_path:h}"
case "${mode}" in
  run) open -n "${app_path}" ;;
  --verify) open -n "${app_path}"; osascript -e 'application id "com.local.codex-perplexity-adapter.resize-test" is running' ;;
  --debug) lldb "${app_path}/Contents/MacOS/CodexPerplexityAdapter" ;;
  --logs|--telemetry) open -n "${app_path}"; tail -F "${HOME}/Library/Logs/Codex Perplexity Adapter Test.log" ;;
  *) print -u2 'usage: script/build_and_run.sh [run|--verify|--debug|--logs|--telemetry]'; exit 2 ;;
esac
