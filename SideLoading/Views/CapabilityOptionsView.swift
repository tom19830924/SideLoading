import SwiftUI

struct CapabilityOptionsView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("額外權限").font(.headline)
            Text("預設保留全部權限。若不需要某項功能，可勾選略過；會套用到下方列出的主 App 與 Extension。")
                .font(.callout).foregroundStyle(.secondary)
            ForEach($model.capabilityOptions) { $option in
                CapabilityOptionRow(option: $option)
            }
            Label("App Groups、共享鑰匙圈、基本 HealthKit 與必要簽署權限會保留。", systemImage: "lock.shield")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(14)
        .background(.orange.opacity(0.08), in: .rect(cornerRadius: 8))
        .disabled(model.isBusy)
    }
}
