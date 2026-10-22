import SwiftUI

struct SigningView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        SectionCard(number: "3", title: "使用你的本機憑證", subtitle: "Apple 帳號只在官方 Xcode 登入，此工具不接收密碼。") {
            if model.environment?.identities.isEmpty != false {
                Label("尚未找到 Apple Development 憑證", systemImage: "key")
                    .foregroundStyle(.secondary)
            } else {
                Picker("簽署憑證", selection: $model.selectedIdentityID) {
                    Text("請選擇憑證").tag("")
                    ForEach(model.environment?.identities ?? []) { identity in
                        Text(identity.name).tag(identity.id)
                    }
                }.disabled(model.isBusy || model.useExistingSignature)
                if let identity = model.identity {
                    Text("Team ID：\(identity.teamID)").font(.callout.monospaced()).foregroundStyle(.secondary)
                }
            }
            if !model.capabilityOptions.isEmpty && !model.useExistingSignature {
                CapabilityOptionsView(model: model)
            }
            DisclosureGroup("進階選項") {
                VStack(alignment: .leading, spacing: 12) {
                    Toggle("IPA 已為這支手機簽署，直接驗證並安裝", isOn: $model.useExistingSignature)
                    Toggle("保留原始 Bundle ID", isOn: $model.keepIdentifier)
                        .disabled(model.useExistingSignature)
                    Text("預設會產生屬於你的固定 App ID，避免與作者衝突。重新安裝時請使用相同憑證團隊與選項；改用不同 App ID 會變成另一個 App。")
                        .font(.callout).foregroundStyle(.secondary)
                }.padding(.top, 10).disabled(model.isBusy)
            }
        }
    }
}
