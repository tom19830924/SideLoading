import SwiftUI

struct SidebarView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 12) {
                Image(systemName: "arrow.down.app.fill")
                    .font(.largeTitle).foregroundStyle(.indigo).accessibilityHidden(true)
                Text("SideLoading").font(.title2).bold()
                Text("你的私人安裝工具").foregroundStyle(.secondary)
            }
            Divider()
            Label("安裝 IPA", systemImage: "iphone.and.arrow.forward")
                .font(.headline).foregroundStyle(.indigo)
                .padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(.indigo.opacity(0.1), in: .rect(cornerRadius: 10))
            VStack(alignment: .leading, spacing: 14) {
                Label("Mac 本機處理", systemImage: "desktopcomputer")
                Label("不收集 Apple 帳密", systemImage: "lock.shield")
                Label("使用系統鑰匙圈", systemImage: "key")
            }.font(.callout).foregroundStyle(.secondary)
            Spacer()
            VStack(alignment: .leading, spacing: 8) {
                Label("有效期依描述檔決定", systemImage: "calendar.badge.clock")
                    .font(.callout).bold()
                Text("到期前準備有效憑證與描述檔，再以相同 App ID 重簽安裝。原始 IPA 會保留。")
                    .font(.callout).foregroundStyle(.secondary)
            }
            Button("第一次使用", systemImage: "book") { model.showSetup = true }
                .buttonStyle(.bordered).controlSize(.large)
        }
        .padding(22)
        .frame(maxHeight: .infinity)
        .background(.regularMaterial)
    }
}
