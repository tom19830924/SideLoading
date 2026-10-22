import SwiftUI
import UniformTypeIdentifiers

@MainActor @Observable
final class InstallerModel {
    var environment: EnvironmentStatus?
    var ipa: IPAInfo?
    var ipaURL: URL?
    var selectedDeviceID = ""
    var selectedIdentityID = ""
    var useExistingSignature = false
    var keepIdentifier = false
    var capabilityOptions: [CapabilityOption] = []
    var isBusy = false
    var status = "選擇 IPA，開始準備安裝。"
    var stage = "ready"
    var errorMessage: String?
    var showSetup = false
    var logs: [String] = []
    var receipt: InstallReceipt?
    private let backend = BackendRunner()

    var device: Device? { environment?.devices.first { $0.id == selectedDeviceID } }
    var identity: SigningIdentity? { environment?.identities.first { $0.id == selectedIdentityID } }
    var canInstall: Bool {
        !isBusy && ipa != nil && ipa?.encrypted == false && environment?.xcodeReady == true
        && device?.available == true && device?.developerMode != "disabled"
        && (useExistingSignature || identity != nil)
    }
    var readiness: String {
        if ipa == nil { return "先選擇要安裝的 IPA 檔案。" }
        if ipa?.encrypted == true { return "這個 IPA 受到加密保護，請取得開發者提供的側載版本。" }
        if environment?.xcodeReady != true { return "請先完成 Xcode 設定。" }
        if device == nil { return "選擇你的 iPhone。" }
        if device?.available != true { return "請用 USB 連接 iPhone、解鎖並信任這台 Mac。" }
        if device?.developerMode == "disabled" { return "請先在 iPhone 開啟開發者模式。" }
        if !useExistingSignature && identity == nil { return "請先在 Xcode 建立 Apple Development 憑證。" }
        return "準備完成，安裝前請保持 iPhone 解鎖。"
    }

    func start() async {
        await refresh()
        let arguments = CommandLine.arguments
        if let index = arguments.firstIndex(of: "--ipa"), arguments.indices.contains(index + 1) {
            await importIPA(URL(filePath: arguments[index + 1]))
        }
    }

    func refresh() async {
        guard !isBusy else { return }
        isBusy = true
        errorMessage = nil
        status = "讀取 Xcode、裝置與本機公開憑證…"
        defer { isBusy = false }
        do {
            let response = try await backend.run(EnvironmentStatus.self, arguments: ["environment"])
            environment = response
            if !response.devices.contains(where: { $0.id == selectedDeviceID }) { selectedDeviceID = "" }
            if !response.identities.contains(where: { $0.id == selectedIdentityID }) { selectedIdentityID = "" }
            if response.identities.count == 1 { selectedIdentityID = response.identities[0].id }
            status = "環境檢查完成。"
            response.warnings.forEach(appendLog)
        } catch { report(error) }
    }

    func refreshAction() { Task { await refresh() } }

    func chooseIPA() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "ipa") ?? .data]
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = "選擇 IPA"
        if panel.runModal() == .OK, let url = panel.url { Task { await importIPA(url) } }
    }

    func acceptDrop(_ urls: [URL]) -> Bool {
        guard !isBusy, let url = urls.first, url.pathExtension.lowercased() == "ipa" else { return false }
        Task { await importIPA(url) }
        return true
    }

    func importIPA(_ url: URL) async {
        guard !isBusy else { return }
        isBusy = true
        ipa = nil
        ipaURL = nil
        capabilityOptions = []
        receipt = nil
        errorMessage = nil
        stage = "inspect"
        status = "檢查 IPA 內容…"
        defer { isBusy = false }
        do {
            ipa = try await backend.run(IPAInfo.self, arguments: ["inspect", url.path])
            capabilityOptions = ipa?.optionalCapabilities ?? []
            ipaURL = url
            stage = "ready"
            status = "IPA 已就緒。"
            appendLog("已檢查 \(url.lastPathComponent)")
        } catch { report(error) }
    }

    func install() {
        guard canInstall, let ipaURL, let device else { return }
        var arguments = ["install", ipaURL.path, "--device", device.id, "--mode", useExistingSignature ? "existing" : "automatic"]
        if !useExistingSignature, let identity {
            arguments += ["--team", identity.teamID, "--identity", identity.id]
        }
        if keepIdentifier { arguments.append("--keep-identifier") }
        if !useExistingSignature {
            for option in capabilityOptions where option.isOmitted {
                arguments += ["--omit-capability", option.id]
            }
        }
        isBusy = true
        errorMessage = nil
        receipt = nil
        Task {
            defer { isBusy = false }
            do {
                receipt = try await backend.run(InstallReceipt.self, arguments: arguments) { [weak self] message, stage in
                    self?.status = message
                    self?.stage = stage
                    self?.appendLog(message)
                }
                stage = "done"
                status = "安裝完成。請在 iPhone 開啟 App。"
                appendLog(status)
            } catch { report(error) }
        }
    }

    func cancel() {
        status = "正在停止作業…"
        backend.cancel()
    }

    func openXcode() {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.apple.dt.Xcode") else {
            errorMessage = "找不到 Xcode。請從 Mac App Store 安裝 Apple 的 Xcode。"
            return
        }
        NSWorkspace.shared.openApplication(at: url, configuration: .init()) { _, error in
            if let error {
                Task { @MainActor [weak self] in self?.report(error) }
            }
        }
    }

    func revealIPA() {
        if let ipaURL { NSWorkspace.shared.activateFileViewerSelecting([ipaURL]) }
    }

    func copyLogs() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(logs.joined(separator: "\n"), forType: .string)
    }

    private func appendLog(_ text: String) {
        logs.append(text)
        if logs.count > 100 { logs.removeFirst(logs.count - 100) }
    }

    private func report(_ error: Error) {
        stage = "error"
        status = "作業未完成，請查看下方說明。"
        errorMessage = error.localizedDescription
        appendLog(error.localizedDescription)
    }
}
