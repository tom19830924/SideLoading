import SwiftUI
import UniformTypeIdentifiers

@MainActor @Observable
final class InstallerModel {
    var environment: EnvironmentStatus?
    var ipa: IPAInfo?
    var ipaURL: URL?
    var selectedDeviceID = ""
    var certificateURL: URL?
    var profileURL: URL?
    var extensionProfiles: [String: URL] = [:]
    var password = ""
    var bundleIdentifier = ""
    var appGroupMappings = ""
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
    var canInstall: Bool {
        !isBusy && ipa != nil && ipa?.encrypted == false && environment?.runtimeReady == true
        && device?.available == true
        && certificateURL != nil && profileURL != nil
    }
    var readiness: String {
        if ipa == nil { return "先選擇要安裝的 IPA 檔案。" }
        if ipa?.encrypted == true { return "這個 IPA 受到加密保護，請取得開發者提供的側載版本。" }
        if environment?.runtimeReady != true { return "請確認使用完整 App，並連接手機後重新整理。" }
        if device == nil { return "選擇你的 iPhone。" }
        if device?.available != true { return "請用 USB 連接 iPhone、解鎖並信任這台 Mac。" }
        if certificateURL == nil { return "選擇包含憑證與私鑰的 P12／PFX 檔案。" }
        if profileURL == nil { return "選擇主 App 的描述檔。" }
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
        status = "讀取 USB 手機清單…"
        defer { isBusy = false }
        do {
            let response = try await backend.run(EnvironmentStatus.self, arguments: ["environment"])
            environment = response
            if !response.devices.contains(where: { $0.id == selectedDeviceID }) { selectedDeviceID = "" }
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
        extensionProfiles = [:]
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
        guard canInstall, let ipaURL, let device, let certificateURL, let profileURL else { return }
        var arguments = ["install", "--ipa", ipaURL.path, "--device", device.id,
                         "--certificate", certificateURL.path, "--profile", profileURL.path, "--password-stdin"]
        for (identifier, profile) in extensionProfiles.sorted(by: { $0.key < $1.key }) {
            arguments += ["--extension-profile", "\(identifier)=\(profile.path)"]
        }
        let identifier = bundleIdentifier.trimmingCharacters(in: .whitespacesAndNewlines)
        if !identifier.isEmpty { arguments += ["--bundle-id", identifier] }
        for mapping in appGroupMappings.split(whereSeparator: \.isNewline) {
            let value = mapping.trimmingCharacters(in: .whitespacesAndNewlines)
            if !value.isEmpty { arguments += ["--app-group", value] }
        }
        for option in capabilityOptions where option.isOmitted {
            arguments += ["--omit-capability", option.id]
        }
        let secretInput = Data(password.utf8)
        isBusy = true
        errorMessage = nil
        receipt = nil
        Task {
            defer { isBusy = false; password = "" }
            do {
                receipt = try await backend.run(InstallReceipt.self, arguments: arguments, input: secretInput) { [weak self] message, stage in
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

    private func chooseSigningFile(extensions: [String], prompt: String) -> URL? {
        guard !isBusy else { return nil }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = extensions.compactMap { UTType(filenameExtension: $0) }
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = prompt
        return panel.runModal() == .OK ? panel.url : nil
    }

    func chooseCertificate() {
        if let url = chooseSigningFile(extensions: ["p12", "pfx"], prompt: "選擇憑證") {
            certificateURL = url
            password = ""
            receipt = nil
        }
    }

    func chooseProfile() {
        if let url = chooseSigningFile(extensions: ["mobileprovision"], prompt: "選擇描述檔") {
            profileURL = url
            receipt = nil
        }
    }

    func chooseExtensionProfile(_ identifier: String) {
        if let url = chooseSigningFile(extensions: ["mobileprovision"], prompt: "選擇 Extension 描述檔") {
            extensionProfiles[identifier] = url
            receipt = nil
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
