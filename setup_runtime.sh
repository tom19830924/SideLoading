#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
uv_binary="${UV_BIN:-$(command -v uv || true)}"
[[ -n "$uv_binary" ]] || { print -u2 "建置環境需要 uv：https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
export UV_CACHE_DIR="$PWD/.build/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.build/python"
"$uv_binary" python install --no-bin 3.12.13
runtime_source="$UV_PYTHON_INSTALL_DIR/cpython-3.12.13-macos-aarch64-none"
[[ -x "$runtime_source/bin/python3.12" ]] || { print -u2 "目前打包腳本支援 Apple Silicon Mac。"; exit 1; }
mkdir -p .build
if [[ ! -x .build/Runtime/bin/python3 ]]; then
    /usr/bin/ditto "$runtime_source" .build/Runtime
fi
"$uv_binary" pip sync --python .build/Runtime/bin/python3 --break-system-packages --require-hashes requirements-runtime.lock
print "完成：獨立 Python 與固定版本套件已準備在 .build/Runtime。"
