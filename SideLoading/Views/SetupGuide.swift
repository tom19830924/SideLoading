import SwiftUI

struct SetupGuide: View {
    let model: InstallerModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Label("第一次設定", systemImage: "lock.shield").font(.title2).bold()
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text("只需把 Apple 帳號登入到 Apple 自己的 Xcode。SideLoading 沒有登入頁，也不讀取或儲存你的密碼。")
                .foregroundStyle(.secondary)
            Divider()
            GuideStep(number: "1", title: "在 Xcode 登入 Apple 帳號", text: "開啟 Xcode → Settings（設定）→ Apple Accounts（較舊版本稱 Accounts），按 ＋ 加入你的免費 Apple 帳號。登入及驗證碼只在 Xcode 輸入。")
            GuideStep(number: "2", title: "建立開發用憑證", text: "在同一個頁面選擇你的帳號和 Personal Team，按 Manage Certificates，按 ＋ 選 Apple Development。完成後回到 SideLoading 按「重新整理」。")
            GuideStep(number: "3", title: "連接並信任這台 Mac", text: "用 USB 線連接 iPhone、解鎖，在手機上選「信任」。第一次可在 Xcode 的 Window → Devices and Simulators 中完成配對。")
            GuideStep(number: "4", title: "開啟 iPhone 開發者模式", text: "在 iPhone 的「設定 → 隱私權與安全性 → 開發者模式」開啟，依畫面重新啟動並確認。若看不到此選項，先完成上一個步驟。")
            Divider()
            Text("免費帳號一般限同時 3 個 App，簽署有效期為 7 天。到期前重新安裝即可續簽；不要先刪掉手機上的 App，以免遺失資料。部分 App 的特殊權限無法用免費帳號簽署。")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Button("開啟官方 Xcode", systemImage: "hammer", action: model.openXcode)
                    .buttonStyle(.borderedProminent).controlSize(.large)
                Spacer()
                if let url = URL(string: "https://developer.apple.com/help/account/basics/about-your-developer-account") {
                    Link("Apple 帳號與簽署說明", destination: url)
                }
            }
        }
        .padding(28)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
