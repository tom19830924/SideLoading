import SwiftUI

struct InstallFooter: View {
    @Bindable var model: InstallerModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Divider()
            HStack(spacing: 16) {
                if model.isBusy { ProgressView().controlSize(.small) }
                VStack(alignment: .leading, spacing: 5) {
                    Text(model.isBusy || model.stage == "done" ? model.status : model.readiness)
                        .font(.callout)
                    Text("安裝時，Xcode 會直接向 Apple 取得簽署許可。")
                        .font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                if model.isBusy {
                    Button("取消", action: model.cancel)
                } else {
                    Button("簽署並安裝", systemImage: "arrow.down.to.line", action: model.install)
                        .buttonStyle(.borderedProminent).controlSize(.large)
                        .disabled(!model.canInstall)
                }
            }.padding(.horizontal, 24).padding(.bottom, 18)
        }
    }
}
