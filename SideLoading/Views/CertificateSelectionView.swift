import SwiftUI

struct CertificateSelectionView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        SectionCard(number: "3", title: "選擇簽署憑證", subtitle: "提供含憑證與私鑰的 P12／PFX；不需要登入 Apple 帳號。") {
            HStack {
                Label(model.certificateURL?.lastPathComponent ?? "尚未選擇 P12／PFX", systemImage: "key")
                    .lineLimit(1).truncationMode(.middle)
                    .help(model.certificateURL?.path ?? "選擇你提供的簽署憑證")
                Spacer()
                Button("選擇憑證", systemImage: "folder", action: model.chooseCertificate)
                    .accessibilityIdentifier("chooseCertificate")
            }
            SecureField("P12 密碼（無密碼可留空）", text: $model.password)
                .textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("certificatePassword")
            Text("密碼只用於本次作業，不會儲存。私鑰會放入暫存鑰匙圈供簽署使用，結束後清理。")
                .font(.caption).foregroundStyle(.secondary)
        }
        .disabled(model.isBusy)
    }
}
