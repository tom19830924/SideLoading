import SwiftUI

struct ReceiptView: View {
    let receipt: InstallReceipt

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("\(receipt.name) 已安裝", systemImage: "checkmark.circle.fill")
                .font(.headline).foregroundStyle(.green)
            Text("裝置：\(receipt.deviceName)")
            if let date = receipt.expirationDate {
                Text("簽署到期：\(date.formatted(date: .abbreviated, time: .shortened))")
            }
            Text("到期時間由你提供的描述檔決定；重新安裝不會改變它的到期日。")
                .font(.callout).foregroundStyle(.secondary)
            Text("重簽 IPA：\(receipt.output)").font(.caption).textSelection(.enabled)
            Button("在 Finder 顯示重簽 IPA", systemImage: "folder") {
                NSWorkspace.shared.activateFileViewerSelecting([URL(filePath: receipt.output)])
            }
            Text("請在 iPhone 點開 App 確認能正常執行。若顯示不受信任的開發者，請到「設定 → 一般 → VPN 與裝置管理」信任你的開發者帳號。")
                .font(.callout).foregroundStyle(.secondary)
        }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
            .background(.green.opacity(0.08), in: .rect(cornerRadius: 12))
    }
}
