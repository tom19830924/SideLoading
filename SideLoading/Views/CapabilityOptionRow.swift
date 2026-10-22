import SwiftUI

struct CapabilityOptionRow: View {
    @Binding var option: CapabilityOption

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Toggle("略過\(option.title)", isOn: $option.isOmitted)
                .accessibilityHint(option.impact)
            Text(option.impact).font(.callout).foregroundStyle(.secondary)
            Text("要求此權限：\(option.bundles.joined(separator: "、"))")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
