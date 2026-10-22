# SideLoading

自己建置的 macOS IPA 安裝工具。目標是將開發者提供的 IPA（例如 JHenTai）安裝到自己的 iPhone。

**沒有 Apple 帳密輸入欄位，沒有第三方簽署服務，也不使用 AltStore、SideStore、iloader 或 Sideloadly。**

## 使用方式

1. 在專案根目錄執行 `./build_app.sh`，產生 `SideLoading.app`；之後直接開啟這個 App。
2. 按「第一次使用」，按照指引在 **Apple 官方 Xcode** 登入 Apple 帳號並建立 Apple Development 憑證。
3. 用 USB 連接自己的 iPhone、解鎖並信任 Mac，開啟 iPhone 的開發者模式。
4. 在 SideLoading 選擇 IPA、自己的 iPhone、自己的簽署憑證。
5. 按「簽署並安裝」。只有 Apple 的 `devicectl` 回報成功才會顯示安裝完成。
6. 在手機開啟 App；必要時到「設定 → 一般 → VPN 與裝置管理」信任自己的開發者帳號。

免費 Apple 帳號的簽署通常 7 天到期，同時最多 3 個 App。請在到期前連接 Mac，使用相同 IPA、團隊與選項重新簽署安裝；不要先刪除手機上的 App。Xcode 可能重用尚未過期的描述檔，因此重新安裝不一定立即延長期限，實際日期以完成畫面顯示為準。

### 視窗與捲動

主視窗可調整大小，最小內容尺寸為 940×700。側欄、頂部操作列與底部安裝列固定；只有中央表單可垂直捲動。設定指引固定為 760pt 寬、依內容自適應高度，最高 700pt；內容超出上限時才可垂直捲動。文字大小則遵循 macOS 的系統輔助功能設定。

0.2.2 依使用者要求只編譯打包，未執行自動測試或實際操作驗證。

## 帳密與資料的處理

- 登入及雙重認證只在 Apple 的 Xcode 介面完成。
- 本程式透過 `security find-identity` 與 `security find-certificate` 讀取本機公開憑證資訊，**不讀取 Apple 密碼，也不匯出私鑰**。
- `codesign` 使用系統鑰匙圈中的私鑰進行本機簽署，macOS 可能顯示鑰匙圈授權提示。
- `xcodebuild` 使用 Xcode 管理的登入狀態，直接向 Apple 登記裝置、App ID 與取得 provisioning profile。這個步驟需要網路，會在所選 Apple 開發者團隊中建立或更新相關資源。
- IPA 在隨機的本機暫存資料夾解開與修改，原始檔不變。一般完成、失敗與取消時會清理暫存內容。
- `devicectl` 將 App 傳送到所選 iPhone。沒有 IPA 上傳網站、遙測或更新服務。
- 作業紀錄只留在記憶體，可能包含裝置／憑證資訊；只有按「複製紀錄」才會放到剪貼簿。

## 建置

需要 macOS 14+、完整 Xcode（包含 Swift 6.2+、iOS SDK、devicectl 及 Python 3）。建議使用支援手機 iOS 版本的 Xcode，並先完成首次啟動設定。

### 在 Xcode 開發與執行

開啟 `SideLoading.xcodeproj`，選擇 `SideLoading` scheme 與「My Mac」，按 `⌘R`。這個 target 是完整的 macOS App bundle，會包含 Bundle Identifier、`Info.plist` 和 `installer.py`，因此會直接顯示 GUI 並可進行中斷點除錯。

### 產生自用 App

在專案根目錄執行：

```sh
./build_app.sh
```

腳本會以 Release 設定產生 Apple Silicon 專用的 `SideLoading.app`，並確認簽章與 `installer.py` 資源完整後才取代舊版本。這個 App 可複製到其他 Apple Silicon Mac；首次開啟時可能需要在 macOS 安全性設定中手動允許。其他 Mac 仍須安裝完整 Xcode，並由使用者在自己的 Xcode 登入 Apple 帳號及建立 Apple Development 憑證。

SwiftUI 介面與 Python 標準函式庫引擎的程式碼均在此專案中。沒有套件下載或第三方函式庫。Mac App 使用本機簽章，並未經 Developer ID 公證；此產物供個人自行建置與使用。

### 測試

```sh
python3 -m unittest discover -s SideLoadingTests/Python -v
```

在 Xcode 選擇 `Product → Test` 或按 `⌘U`，執行 `SideLoadingTests`。測試涵蓋 IPA 路徑穿越、符號連結、解壓縮上限、Mach-O 加密檢查、裝置清單格式、簽署到期／裝置／Bundle ID／憑證／權限比對，以及原生 UI 與本機程序的資料通道。

`0.1.1` 修正自動簽署與指定憑證衝突：向 Xcode 取得描述檔時使用 `Apple Development`，取得後仍驗證描述檔包含使用者選定的憑證，並使用該憑證簽署實際 IPA。新增的回歸測試重播此 Xcode 限制，避免只測試未簽署建置而漏掉錯誤。

## 簽署方式

預設為每個 Apple 團隊與原始 App ID 產生固定的新 Bundle ID，避免與作者的 App ID 衝突。每個 App／擴充功能會建立只供取得描述檔的最小 Xcode 專案，使用 Xcode 自動簽署。此暫存程式不會安裝到手機；實際安裝的是重新簽署的原始 IPA 內容。

引擎驗證描述檔到期日、目標裝置、App ID、所選憑證與 entitlements，依內到外順序簽署內嵌 framework／dylib／擴充功能，最後交給 `devicectl` 安裝。進階選項可保留原始 Bundle ID，或直接安裝已有適用簽章的 IPA。

### App Extension（0.2.0）

- 列出 IPA 中的 Extension；為每個 Extension 建立真正的 `app-extension` target，保留它的 `NSExtension` 類型並取得獨立描述檔。
- 主 App 與 Extension 使用同一份共享群組對照：換團隊時為 App Groups 加上新團隊後綴，轉換共享鑰匙圈的原團隊前綴，保留共享群組之間的關係。LiveContainer 的 128 個共享鑰匙圈群組會全部保留。
- 支援 `ALTAppGroups` 宣告，包含未簽署的 Widget 與內嵌 SideStore framework；同步更新群組權限及這些 Info.plist 宣告。
- 每個描述檔仍必須授權對應的群組、裝置、Bundle ID 與憑證。某個 Extension 無法取得許可時，會顯示它的名称並停止；不會刪除該 Extension。
- 若 IPA 要求 HealthKit `health-records`，介面會顯示「略過健康紀錄存取」。**預設不勾選**。明確勾選後只移除該項存取，保留基本 HealthKit、其他權限及全部 Extension；依賴健康紀錄存取的功能將不可用。命令列等效選項為 `--omit-health-records`。

App Groups 的名稱若寫死在 App 二進位中，且沒有像 LiveContainer 一樣讀取新團隊或簽章的機制，仍可能無法使用共享資料。本工具更新簽章與已知的 Info.plist 宣告，不會任意改寫二進位中的文字。內建 SideStore 的帳號登入、自動更新與 LiveContainer 的 guest App 簽署設定，亦不會由本工具代為完成或匯出私鑰。

### 額外權限選擇（0.2.1）

匯入 IPA 後，「額外權限」會列出它有宣告的臨床健康紀錄、HealthKit 背景傳遞與記憶體增額。每項顯示要求它的 App／Extension 和省略影響。所有選項預設關閉；勾選「略過」才會從該次重簽的所有相關 bundle 移除。更換或重新匯入 IPA 後重設選擇。

針對本次 LiveContainer 的錯誤，可只勾選「略過臨床健康紀錄」。基本 HealthKit、App Groups、共享鑰匙圈、必要簽署權限及其餘原始權限繼續保留；Extension 不會被刪除。作業紀錄會列出實際略過項目，原始 IPA 不變。此選項不保證所有功能正常，功能驗證由使用者進行。

命令列支援重複指定 `--omit-capability health-records`、`--omit-capability healthkit-background`、`--omit-capability increased-memory`；原有 `--omit-health-records` 仍可使用。已有簽章直接安裝模式不接受權限省略。

0.2.1 依使用者要求只建置交付，未執行自動測試、Apple 描述檔申請或實機安裝；下方 0.2.0 測試紀錄屬於先前版本。

## 實際限制

- **不能從一般 Safari 網頁下載未簽署 IPA 後直接執行。**這個版本需要 Mac 和 Xcode。
- 不能解密 App Store 加密的 IPA；需要作者提供可側載版本。
- 暫不支援 Watch App、App Clip、含符號連結的 IPA、受限制的特殊權限，以及其他不受帳號許可的 capabilities。無法處理時會停止並顯示原因，不會靜默刪除權限。
- 修改 Bundle ID 可能影響部分 App 的登入、推播或共享資料功能，無法保證所有網路下載的 IPA 都能正常執行。
- 支援此程式的 macOS 最低為 14；目前安裝引擎以 iOS 17+ 的 `devicectl` 流程為目標。
- 「安裝完成」代表 iPhone 接受安裝，App 是否成功啟動及內部功能仍需在實機確認。
- 取消若發生在手機已開始安裝後，請在手機查看實際狀態；不會自動刪除已裝入的 App。

## 本次驗證範圍

JHenTai 官方 `8.0.16+334` IPA 已下載並比對 GitHub 發布資訊的 SHA-256：

`fb2710240451a884d843bf2b3049937f15b7088d44443b0146ce9afb42abe236`

檢查結果：最低 iOS 13.0、主執行檔未加密、沒有 App Extension。

2026-09-11，使用修正後的 `0.1.1`、使用者選定的本機憑證及 Xcode，已完成簽署並安裝到 iPhone 17 Pro Max（iOS 26.6.1），`devicectl` 回報安裝成功。本次描述檔的到期日為 2027-09-11（以實際所選團隊的許可為準）。後續啟動檢查被手機鎖定狀態阻止，尚未確認 App 成功啟動與內部功能。

`0.2.0` 使用工作目錄中的 `LiveContainer+SideStore.ipa`（LiveContainer 3.8.0）驗證：

- 26 個 Python 測試及 2 個原生程序整合測試通過；包含 HealthKit 拒絕診斷、群組共用、未簽署 Widget、128 個鑰匙圈群組及內嵌 framework 的群組宣告。
- 真正 Xcode 的 Extension target 編譯通過（此編譯檢查停用簽署）。
- 保留原始權限，使用已選定的真實 Xcode 團隊實測：`LaunchAppExtension.appex` 已取得描述檔並通過群組與憑證檢查；`LiveProcess.appex` 仍被 Apple/Xcode 以免費團隊不支援 HealthKit Access 拒絕。尚未完成整份 IPA 的 Apple 簽署或實機安裝；不可將本機測試通過解讀為安裝完成。

## 官方參考

- [Apple：免費帳號與 Personal Team 限制](https://developer.apple.com/help/account/basics/about-your-developer-account)
- [Apple：在實機執行 App 與簽署設定](https://developer.apple.com/documentation/xcode/running-your-app-on-simulated-or-physical-devices)
- [JHenTai 官方安裝說明](https://github.com/jiangtian616/JHenTai)
- [本次下載的官方發佈版本](https://github.com/jiangtian616/JHenTai/releases/tag/v8.0.16%2B334)
- [Apple：iOS 帳號能力表](https://developer.apple.com/help/account/reference/supported-capabilities-ios)
- [LiveContainer：共享群組與團隊識別碼](https://github.com/LiveContainer/LiveContainer/blob/main/LiveContainer/LCSharedUtils.m)
- [LiveContainer + SideStore 官方設定說明](https://livecontainer.github.io/docs/installation/lc_sidestore)
