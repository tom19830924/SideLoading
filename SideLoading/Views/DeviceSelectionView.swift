import SwiftUI

struct DeviceSelectionView: View {
    @Bindable var model: InstallerModel

    var body: some View {
        SectionCard(number: "2", title: "連接你的 iPhone", subtitle: "使用 USB 線連接、解鎖，並在手機上點選「信任」。") {
            HStack {
                Picker("安裝到", selection: $model.selectedDeviceID) {
                    Text("請選擇你的 iPhone").tag("")
                    ForEach(model.environment?.devices ?? []) { device in
                        Text("\(device.name) · iOS \(device.osVersion) · \(device.status)").tag(device.id)
                    }
                }
                .disabled(model.isBusy)
                Button("重新整理", systemImage: "arrow.clockwise", action: model.refreshAction)
                    .disabled(model.isBusy)
            }
            if let device = model.device {
                Label("\(device.model) — \(device.status)", systemImage: device.available ? "iphone" : "cable.connector")
                    .font(.callout).foregroundStyle(device.available ? Color.secondary : Color.orange)
            } else if model.environment?.devices.isEmpty == true {
                Text("尚未找到 iPhone。連接後請按「重新整理」。").foregroundStyle(.secondary)
            }
        }
    }
}
