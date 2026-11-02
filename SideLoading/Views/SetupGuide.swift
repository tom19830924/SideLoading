import SwiftUI

struct SetupGuide: View {
    let model: InstallerModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Label("第一次使用", systemImage: "lock.shield").font(.title2).bold()
                Spacer()
                Button("完成") { dismiss() }.keyboardShortcut(.cancelAction)
            }
            Text("準備你自己的 IPA、P12／PFX 與描述檔。使用本 App 不需要安裝 Xcode 或登入 Apple 帳號。")
                .foregroundStyle(.secondary)
            GuideStep(number: "1", title: "選取原始 IPA", text: "選擇或拖入開發者提供的未加密 IPA。工具會建立工作副本，原始檔保持不變。")
            GuideStep(number: "2", title: "連接並選取手機", text: "使用 USB 連接 iPhone 或 iPad、解鎖，並在 Finder 與手機完成「信任此電腦」。回到 App 按重新整理並選取手機。需要時，在手機開啟開發者模式。")
            GuideStep(number: "3", title: "選擇 P12 與輸入密碼", text: "P12／PFX 必須同時包含簽署憑證及私鑰。若檔案沒有密碼，密碼欄留空即可。密碼不會儲存。")
            GuideStep(number: "4", title: "選取描述檔並安裝", text: "選擇主 App 的描述檔；含 Extension 時可分別指定。描述檔必須允許所選憑證、App ID、必要權限與手機。按「簽署並安裝」，完成後請在手機確認 App 能正常開啟。")
            Divider()
            Text("新的 IPA 預設放在來源資料夾，檔名加上 -signed；同名時自動加編號。安裝失敗時，已重簽的 IPA 仍會保留。")
                .font(.callout).foregroundStyle(.secondary)
        }.padding(28).frame(maxWidth: .infinity, alignment: .leading)
    }
}
