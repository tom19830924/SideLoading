#!/bin/zsh
set -euo pipefail

if [[ ! -d SideLoading.xcodeproj || ! -d SideLoading ]]; then
    print -u2 "請在 SideLoading 專案根目錄執行：./build_app.sh"
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
[[ -f "$built_app/Contents/Resources/installer.py" ]] || { print -u2 "建置產物缺少 installer.py"; exit 1; }
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

print "完成：$output_app（Apple Silicon / Release）"
