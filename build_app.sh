#!/bin/zsh
set -euo pipefail

if [[ ! -d SideLoading.xcodeproj || ! -d SideLoading ]]; then
    print -u2 "請在 SideLoading 專案根目錄執行：./build_app.sh"
    exit 1
fi

if [[ ! -x .build/Runtime/bin/python3 ]]; then
    print -u2 "請先執行 ./setup_runtime.sh 準備內附執行環境。"
    exit 1
fi

build_root="$(/usr/bin/mktemp -d /private/tmp/sideloading-build.XXXXXX)"
build_log="$(/usr/bin/mktemp /private/tmp/sideloading-build-log.XXXXXX)"
staging_app=".SideLoading.app.staging"
output_app="SideLoading.app"

cleanup() {
    /bin/rm -rf "$build_root" "$build_log" "$staging_app"
}
trap cleanup EXIT

if ! /usr/bin/xcodebuild \
    -project SideLoading.xcodeproj \
    -scheme SideLoading \
    -configuration Release \
    -derivedDataPath "$build_root" \
    ARCHS=arm64 \
    ONLY_ACTIVE_ARCH=YES \
    CODE_SIGN_IDENTITY=- \
    build >"$build_log" 2>&1; then
    print -u2 "建置失敗。以下是最後 60 行記錄："
    /usr/bin/tail -n 60 "$build_log" >&2
    exit 1
fi

built_app="$build_root/Build/Products/Release/SideLoading.app"
built_binary="$built_app/Contents/MacOS/SideLoading"

[[ -d "$built_app" ]] || { print -u2 "找不到建置完成的 SideLoading.app"; exit 1; }
for resource in installer.py resign_cli.py ipa_resigner.py signing_keychain.py device_backend.py Runtime/bin/python3 LICENSE; do
    [[ -f "$built_app/Contents/Resources/$resource" ]] || { print -u2 "建置產物缺少 $resource"; exit 1; }
done
/usr/bin/codesign --verify --deep --strict "$built_app"

architectures="$(/usr/bin/lipo -archs "$built_binary")"
[[ "$architectures" == "arm64" ]] || { print -u2 "建置產物不是 arm64-only：$architectures"; exit 1; }

if [[ -e "$output_app" && ! -d "$output_app" ]]; then
    print -u2 "$output_app 已存在但不是 App bundle，為保護檔案而停止。"
    exit 1
fi

/usr/bin/ditto "$built_app" "$staging_app"
/bin/rm -rf "$output_app"
/bin/mv "$staging_app" "$output_app"

# A separate relocatable command-line distribution; no Xcode or uv at runtime.
mkdir -p dist/SideLoadingCLI/Resources
/usr/bin/ditto "$output_app/Contents/Resources/Runtime" dist/SideLoadingCLI/Runtime
for resource in installer.py resign_cli.py ipa_resigner.py signing_keychain.py device_backend.py; do
    /bin/cp "$output_app/Contents/Resources/$resource" "dist/SideLoadingCLI/Resources/$resource"
done
/bin/cp resign_ipa dist/SideLoadingCLI/resign_ipa
/bin/cp requirements-runtime.lock dist/SideLoadingCLI/requirements-runtime.lock
/bin/cp README.md dist/SideLoadingCLI/README.md
/bin/cp LICENSE dist/SideLoadingCLI/LICENSE

print "完成：$output_app 與 dist/SideLoadingCLI（Apple Silicon / Release）"
