import SwiftUI

struct SigningView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        SectionCard(number: "4", title: "選擇描述檔", subtitle: "使用你提供的 mobileprovision；會檢查憑證、手機與必要權限。") {
            HStack {
                Label(model.profileURL?.lastPathComponent ?? "尚未選擇主 App 描述檔", systemImage: "doc.badge.gearshape")
                    .lineLimit(1).truncationMode(.middle)
                    .help(model.profileURL?.path ?? "選擇主 App 的描述檔")
                Spacer()
                Button("選擇描述檔", systemImage: "folder", action: model.chooseProfile)
                    .accessibilityIdentifier("chooseMainProfile")
            }
            ExtensionProfilesView(model: model)
            if !model.capabilityOptions.isEmpty { CapabilityOptionsView(model: model) }
            DisclosureGroup("進階選項") {
                VStack(alignment: .leading, spacing: 12) {
                    TextField("Bundle ID（留空依描述檔決定）", text: $model.bundleIdentifier)
                        .textFieldStyle(.roundedBorder)
                    TextField("App Group 對照，每行一筆 group.old=group.new", text: $model.appGroupMappings, axis: .vertical)
                        .lineLimit(2...5).textFieldStyle(.roundedBorder)
                    Text("主 App 預設使用描述檔的 ID；萬用描述檔則保留原始 ID。更換 ID 會被手機視為另一個 App。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(.top, 10)
            }
        }
        .disabled(model.isBusy)
    }
}
