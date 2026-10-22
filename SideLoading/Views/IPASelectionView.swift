import SwiftUI

struct IPASelectionView: View {
    @Bindable var model: InstallerModel
    @State private var isTargeted = false

    var body: some View {
        SectionCard(number: "1", title: "選擇應用程式", subtitle: "將下載的 .ipa 拖進來，或從檔案中選取。") {
            HStack(spacing: 18) {
                Image(systemName: model.ipa == nil ? "shippingbox" : "app.gift")
                    .font(.largeTitle).foregroundStyle(.indigo).accessibilityHidden(true)
                if let ipa = model.ipa {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(ipa.name).font(.title3).bold()
                        Text("\(ipa.version) · \(ipa.formattedSize) · iOS \(ipa.minimumOS)+")
                            .foregroundStyle(.secondary)
                        Text(ipa.bundleID).font(.callout.monospaced()).foregroundStyle(.secondary)
                        if ipa.extensionCount > 0 {
                            Label("包含 \(ipa.extensionCount) 個 Extension，將一併簽署", systemImage: "puzzlepiece.extension")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                        if ipa.encrypted {
                            Label("執行檔受加密保護", systemImage: "lock.trianglebadge.exclamationmark")
                                .foregroundStyle(.orange)
                        } else {
                            Label("IPA 檢查通過", systemImage: "checkmark.circle").foregroundStyle(.green)
                        }
                    }
                } else {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("拖放 IPA 到這裡").font(.headline)
                        Text("檔案只在這台 Mac 上解開與簽署。")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button(model.ipa == nil ? "選擇檔案" : "更換檔案", systemImage: "folder", action: model.chooseIPA)
                    .controlSize(.large).disabled(model.isBusy)
            }
            .padding(20).frame(maxWidth: .infinity, minHeight: 110, alignment: .leading)
            .background(.indigo.opacity(isTargeted ? 0.12 : 0.04), in: .rect(cornerRadius: 12))
            .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(.indigo.opacity(0.3), style: StrokeStyle(lineWidth: 1, dash: [5])) }
            .dropDestination(for: URL.self) { urls, _ in model.acceptDrop(urls) } isTargeted: { isTargeted = $0 }
            if let extensions = model.ipa?.extensions, !extensions.isEmpty {
                DisclosureGroup("查看 Extension") {
                    ForEach(extensions) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name)
                            Text(item.bundleID).font(.caption.monospaced()).foregroundStyle(.secondary)
                        }.padding(.vertical, 4)
                    }
                }
            }
        }
    }
}
