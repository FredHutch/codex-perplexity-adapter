#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h}"
pipx_venv="${HOME}/Library/Application Support/pipx/venvs/codex-perplexity-adapter"
target_arch="${ADAPTER_TARGET_ARCH:-arm64}"
output_dir="${ADAPTER_OUTPUT_DIR:-${project_dir}/dist}"
pyinstaller="${ADAPTER_PYINSTALLER:-${pipx_venv}/bin/pyinstaller}"
codesign_identity="${ADAPTER_CODESIGN_IDENTITY:-Developer ID Application: Andrew Portuguese (SFC29VVF27)}"
entitlements="${project_dir}/macos/entitlements.plist"

case "${target_arch}" in
  arm64)
    build_dir="${project_dir}/build/macos"
    final_app="${output_dir}/Codex Perplexity Adapter.app"
    final_zip="${output_dir}/Codex-Perplexity-Adapter-macOS-arm64.zip"
    ;;
  x86_64)
    build_dir="${project_dir}/build/macos-x86_64"
    final_app="${output_dir}/x86_64/Codex Perplexity Adapter.app"
    final_zip="${output_dir}/Codex-Perplexity-Adapter-macOS-x86_64.zip"
    ;;
  *)
    print -u2 "Unsupported target architecture: ${target_arch}"
    exit 1
    ;;
esac

source_icon="${project_dir}/icon.png"
stage_dir="$(mktemp -d "/private/tmp/codex-perplexity-${target_arch}.XXXXXX")"
app_dir="${stage_dir}/Codex Perplexity Adapter.app"
staged_zip="${stage_dir}/${final_zip:t}"
iconset_dir="${build_dir}/AppIcon.iconset"
app_icon="${build_dir}/AppIcon.icns"
trap 'rm -rf "${stage_dir}"' EXIT

if [[ ! -x "${pyinstaller}" ]]; then
  print -u2 "PyInstaller is not installed in the adapter pipx environment."
  print -u2 "Run: pipx runpip codex-perplexity-adapter install pyinstaller"
  exit 1
fi

rm -rf "${build_dir}" "${final_app}"
rm -f "${final_zip}"
mkdir -p "${build_dir}" "${final_app:h}" "${app_dir}/Contents/MacOS" "${app_dir}/Contents/Resources"

if [[ ! -f "${source_icon}" ]]; then
  print -u2 "Missing app icon: ${source_icon}"
  exit 1
fi

mkdir -p "${iconset_dir}"
sips -z 16 16 "${source_icon}" --out "${iconset_dir}/icon_16x16.png" >/dev/null
sips -z 32 32 "${source_icon}" --out "${iconset_dir}/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "${source_icon}" --out "${iconset_dir}/icon_32x32.png" >/dev/null
sips -z 64 64 "${source_icon}" --out "${iconset_dir}/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "${source_icon}" --out "${iconset_dir}/icon_128x128.png" >/dev/null
sips -z 256 256 "${source_icon}" --out "${iconset_dir}/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "${source_icon}" --out "${iconset_dir}/icon_256x256.png" >/dev/null
sips -z 512 512 "${source_icon}" --out "${iconset_dir}/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "${source_icon}" --out "${iconset_dir}/icon_512x512.png" >/dev/null
sips -z 1024 1024 "${source_icon}" --out "${iconset_dir}/icon_512x512@2x.png" >/dev/null
python3 "${project_dir}/macos/make_icns.py" "${iconset_dir}" "${app_icon}"

export CLANG_MODULE_CACHE_PATH="${build_dir}/clang-module-cache"
export PYINSTALLER_CONFIG_DIR="${build_dir}/pyinstaller-config"

"${pyinstaller}" \
  --noconfirm \
  --clean \
  --onefile \
  --name adapter-server \
  --distpath "${build_dir}/server-dist" \
  --workpath "${build_dir}/server-work" \
  --specpath "${build_dir}" \
  --paths "${project_dir}" \
  --collect-all uvicorn \
  --collect-all fastapi \
  "${project_dir}/macos/server_entry.py"

clang \
  -arch "${target_arch}" \
  -O2 \
  -fobjc-arc \
  -mmacosx-version-min=13.0 \
  -framework AppKit \
  "${project_dir}/macos/AdapterLauncher.m" \
  -o "${app_dir}/Contents/MacOS/CodexPerplexityAdapter"

cp "${build_dir}/server-dist/adapter-server" "${app_dir}/Contents/Resources/adapter-server"
cp "${app_icon}" "${app_dir}/Contents/Resources/AppIcon.icns"
cp "${project_dir}/macos/Info.plist" "${app_dir}/Contents/Info.plist"
chmod +x "${app_dir}/Contents/MacOS/CodexPerplexityAdapter" "${app_dir}/Contents/Resources/adapter-server"
xattr -cr "${app_dir}"
codesign --force --options runtime --timestamp --entitlements "${entitlements}" --sign "${codesign_identity}" \
  "${app_dir}/Contents/Resources/adapter-server"
codesign --force --options runtime --timestamp --sign "${codesign_identity}" \
  "${app_dir}/Contents/MacOS/CodexPerplexityAdapter"
codesign --force --options runtime --timestamp --sign "${codesign_identity}" "${app_dir}"
codesign --verify --deep --strict "${app_dir}"

(cd "${stage_dir}" && /usr/bin/zip -qry "${staged_zip}" "${app_dir:t}")
mv "${staged_zip}" "${final_zip}"

print "Built ${final_zip}"
