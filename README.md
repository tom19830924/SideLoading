# SideLoading

macOS 上的 IPA 重簽與 USB 安裝工具，提供 **SideLoading.app** 圖形介面與 **resign_ipa** 命令列工具。

你提供原始 IPA、P12／PFX 憑證與描述檔，工具在 Mac 本機完成重簽，保留原始檔並產生新的 IPA。App 可接著將重簽的 IPA 安裝到 iPhone／iPad；命令列工具預設只重簽並輸出檔案。

已打包的 App 與 CLI 內附 Python 及所需套件，執行時不需要安裝 Xcode 或登入 Apple 帳號。本工具不會申請憑證、App ID 或描述檔。

支援 **Apple Silicon Mac、macOS 14 或更新版本**。使用前請準備：

- 開發者提供的未加密 `.ipa`。
- 包含簽署憑證及私鑰的 `.p12`／`.pfx`，以及檔案密碼（若有）。
- 符合憑證、App ID、必要權限及目標裝置的 `.mobileprovision`。含 Extension 的 IPA 可能需要額外描述檔。

不支援 App Store 加密 IPA、Watch App、App Clip 或含符號連結的 IPA。重新簽署不會延長描述檔的有效期限。

## 手動建置 App 與 CLI

### 建置需求

- Apple Silicon Mac、macOS 14+。
- 完整 Xcode 與 Swift 6.2+，並完成 Xcode 首次啟動設定。
- [uv](https://docs.astral.sh/uv/getting-started/installation/)。
- 首次準備 Python 與套件時需要網路。

### 建置步驟

取得原始碼並進入專案目錄：

```sh
git clone https://github.com/tom19830924/SideLoading.git
cd SideLoading
```

如果已下載專案，直接切換到該目錄即可；例如目前這台 Mac 的專案位置是 `/Users/user/Desktop/SideLoading`。

依序執行：

```sh
./setup_runtime.sh
./build_app.sh
```

第一個指令準備內附 Python 與固定版本的套件；第二個指令以 Release 設定建置 arm64 App，並同時產生 CLI 發佈包。後續修改程式時可直接執行 `./build_app.sh`；新電腦、刪除執行環境或更新依賴時，再先執行 `./setup_runtime.sh`。

### 產物位置

| 產物 | 專案內位置 | 以目前這台 Mac 為例 |
| --- | --- | --- |
| GUI App | `SideLoading.app` | `/Users/user/Desktop/SideLoading/SideLoading.app` |
| CLI／binary 入口 | `dist/SideLoadingCLI/resign_ipa` | `/Users/user/Desktop/SideLoading/dist/SideLoadingCLI/resign_ipa` |

CLI 的 `resign_ipa` 是啟動檔，會使用同資料夾下的 `Runtime/` 與 `Resources/`。搬移或分享時，請保留整個 `dist/SideLoadingCLI` 資料夾。GUI 則需保留完整 `SideLoading.app`。

若要在 Xcode 中開發或除錯，先執行 `./setup_runtime.sh`，再開啟 `SideLoading.xcodeproj`，選擇 `SideLoading` scheme 與「My Mac」，按 `⌘R`。上述固定產物位置由 `./build_app.sh` 產生。

## 使用 App 與 CLI

### App：重簽並安裝到手機

開啟建置完成的 `SideLoading.app`，或在專案根目錄執行：

```sh
open ./SideLoading.app
```

1. 選擇或拖入原始 IPA。
2. 使用 USB 連接 iPhone／iPad，解鎖並在 Finder 與手機完成「信任此電腦」。開發用簽章需要時，請開啟手機的開發者模式。
3. 按「重新整理」，選取目標手機。
4. 選取 P12／PFX 並輸入密碼；沒有密碼則留空。
5. 選取主 App 描述檔；含 Extension 時可分別指定對應描述檔。
6. 按「簽署並安裝」。完成後可按「在 Finder 顯示重簽 IPA」，並在手機開啟 App 確認功能。

進階選項可指定 Bundle ID、App Group 對照及略過支援的額外權限。安裝失敗時，已成功重簽的 IPA 會保留，錯誤訊息會顯示檔案位置。

### CLI：重簽並輸出 IPA

在專案根目錄執行：

```sh
./dist/SideLoadingCLI/resign_ipa \
  --ipa "/path/to/App.ipa" \
  --p12 "/path/to/signing.p12" \
  --profile "/path/to/App.mobileprovision"
```

`--ipa` 指定原始 IPA，`--p12` 指定憑證與私鑰，`--profile` 指定主 App 描述檔。執行後依提示輸入 P12 密碼，輸入內容不會顯示；無密碼直接按 Enter。這個範例不需要連接手機。

<details>
<summary>使用目前專案檔案的完整路徑範例</summary>

```sh
/Users/user/Desktop/SideLoading/dist/SideLoadingCLI/resign_ipa \
  --ipa "/Users/user/Desktop/SideLoading/JHenTai_8.0.17+338.ipa" \
  --p12 "/Users/user/Desktop/SideLoading/憑證.p12" \
  --profile "/Users/user/Desktop/SideLoading/sparxe_dev.mobileprovision"
```

請將檔案路徑換成你的實際位置。

</details>

預設在原始 IPA 同一個資料夾產生 `App-signed.ipa`；同名時使用 `App-signed-2.ipa`、`App-signed-3.ipa` 等名稱。可用 `--output "/path/to/output/App-signed.ipa"` 指定其他輸出位置，資料夾必須已存在。

### 常用 CLI 選項

| 選項 | 用途 |
| --- | --- |
| `--output PATH`／`-o PATH` | 指定輸出 IPA 路徑。 |
| `--bundle-id ID` | 指定新的主 App Bundle ID，須符合描述檔；預設依描述檔決定。 |
| `--extension-profile ORIGINAL_ID=PATH` | 為 Extension 指定描述檔，以原始 Bundle ID 配對，可重複指定。 |
| `--app-group OLD=NEW` | 指定 App Group 對照，可重複指定。 |
| `--omit-capability NAME` | 略過 `health-records`、`healthkit-background` 或 `increased-memory`，可重複指定。 |
| `--password-env NAME` | 從指定環境變數讀取 P12 密碼，供自動化使用。 |
| `--force` | 允許覆蓋既有輸出；仍禁止覆蓋原始 IPA、憑證與描述檔。 |

查看完整用法：

```sh
./dist/SideLoadingCLI/resign_ipa --help
```

## 透過 Homebrew 安裝

先安裝 [Homebrew](https://brew.sh/)，再執行：

```sh
brew install tom19830924/tap/resign-ipa
```

套件名稱是 **`resign-ipa`**，終端機指令是 **`resign_ipa`**。安裝後可直接從任何資料夾執行：

```sh
resign_ipa --help

resign_ipa \
  --ipa "/path/to/App.ipa" \
  --p12 "/path/to/signing.p12" \
  --profile "/path/to/App.mobileprovision"
```

更新：

```sh
brew update
brew upgrade tom19830924/tap/resign-ipa
```

移除：

```sh
brew uninstall tom19830924/tap/resign-ipa
```

Homebrew 安裝的是 CLI。GUI App 的下載位置為 [GitHub Releases](https://github.com/tom19830924/SideLoading/releases)。

## 授權

本專案採用 [MIT License](LICENSE)。你可以自由使用、修改、散佈及商業使用；散佈原始碼或軟體副本時，需保留原有著作權與授權聲明。軟體依現狀提供，不附帶擔保。

內附的 Python 與第三方套件依各自的授權條款發佈；相關授權檔隨執行環境保留。主要上游專案為 [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) 與 [python-build-standalone](https://github.com/astral-sh/python-build-standalone)。
