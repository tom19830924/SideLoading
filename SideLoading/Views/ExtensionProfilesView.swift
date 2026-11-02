import SwiftUI

struct ExtensionProfilesView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        if let extensions = model.ipa?.extensions, !extensions.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                Text("Extension 描述檔").font(.headline)
                Text("可為每個 Extension 指定描述檔；未指定時，會嘗試使用主描述檔並檢查是否涵蓋。")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(extensions) { item in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(item.name).font(.callout).bold()
                        Text(item.bundleID).font(.caption.monospaced()).textSelection(.enabled)
                        HStack {
                            Text(model.extensionProfiles[item.bundleID]?.lastPathComponent ?? "使用主描述檔（須符合許可）")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            Spacer()
                            Button("選擇 \(item.name) 描述檔") { model.chooseExtensionProfile(item.bundleID) }
                            if model.extensionProfiles[item.bundleID] != nil {
                                Button("清除", systemImage: "xmark.circle") { model.extensionProfiles[item.bundleID] = nil }
                            }
                        }
                    }
                }
            }
        }
    }
}
