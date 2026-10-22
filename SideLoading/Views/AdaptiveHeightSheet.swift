import SwiftUI

private struct AdaptiveSheetContentHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0

    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

struct AdaptiveHeightSheet<Content: View>: View {
    private let width: CGFloat
    private let maximumHeight: CGFloat
    private let content: Content
    @State private var contentHeight: CGFloat

    init(
        width: CGFloat = 760,
        maximumHeight: CGFloat = 700,
        @ViewBuilder content: () -> Content
    ) {
        self.width = width
        self.maximumHeight = maximumHeight
        self.content = content()
        _contentHeight = State(initialValue: maximumHeight)
    }

    var body: some View {
        ScrollView(.vertical) {
            content
                .frame(width: width, alignment: .leading)
                .fixedSize(horizontal: false, vertical: true)
                .background {
                    GeometryReader { proxy in
                        Color.clear.preference(
                            key: AdaptiveSheetContentHeightKey.self,
                            value: proxy.size.height
                        )
                    }
                }
        }
        .scrollDisabled(contentHeight <= maximumHeight)
        .scrollIndicators(contentHeight > maximumHeight ? .automatic : .hidden)
        .frame(width: width, height: min(contentHeight, maximumHeight))
        .onPreferenceChange(AdaptiveSheetContentHeightKey.self) { height in
            guard height > 0 else { return }
            contentHeight = height
        }
    }
}
