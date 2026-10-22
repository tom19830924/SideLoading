import SwiftUI

struct ErrorPanel: View {
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("需要處理一個問題", systemImage: "exclamationmark.triangle").font(.headline)
            Text(message).font(.callout).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(18)
        .background(.orange.opacity(0.1), in: .rect(cornerRadius: 12))
    }
}
