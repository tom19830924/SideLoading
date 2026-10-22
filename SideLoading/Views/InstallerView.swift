import SwiftUI

struct InstallerView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        HStack(spacing: 0) {
            SidebarView(model: model)
                .frame(width: 225)
            Divider()
            VStack(spacing: 0) {
                HStack {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("把 App 帶到你的 iPhone").font(.title2).bold()
                        Text("本機簽署 · Apple 官方工具 · 帳密留在 Xcode")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(24)
                Divider()
                ScrollView {
                    VStack(spacing: 16) {
                        IPASelectionView(model: model)
                        DeviceSelectionView(model: model)
                        SigningView(model: model)
                        if let error = model.errorMessage {
                            ErrorPanel(message: error)
                        }
                        if let receipt = model.receipt {
                            ReceiptView(receipt: receipt)
                        }
                        if !model.logs.isEmpty {
                            DisclosureGroup("作業紀錄") {
                                VStack(alignment: .leading, spacing: 12) {
                                    Text(model.logs.joined(separator: "\n"))
                                        .font(.callout.monospaced()).textSelection(.enabled)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    Button("複製紀錄", systemImage: "doc.on.doc", action: model.copyLogs)
                                }.padding(.top, 12)
                            }.padding(.horizontal, 4)
                        }
                    }.padding(24)
                }
                .background(.quaternary.opacity(0.35))
                InstallFooter(model: model)
            }
        }
        .tint(.indigo)
    }
}
