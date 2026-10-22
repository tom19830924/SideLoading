import SwiftUI

@main
struct SideLoadingApp: App {
    @State private var model = InstallerModel()

    var body: some Scene {
        Window("SideLoading", id: "installer") {
            InstallerView(model: model)
            .frame(minWidth: 940, minHeight: 700)
            .task { await model.start() }
            .onOpenURL { url in Task { await model.importIPA(url) } }
            .sheet(isPresented: $model.showSetup) {
                AdaptiveHeightSheet {
                    SetupGuide(model: model)
                }
            }
        }
        .defaultSize(width: 1040, height: 820)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("開啟 IPA…", action: model.chooseIPA)
                    .keyboardShortcut("o")
                    .disabled(model.isBusy)
            }
        }
    }
}
