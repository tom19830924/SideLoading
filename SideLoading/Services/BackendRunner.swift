import Foundation

/// Owns one local worker. Arguments never pass through a shell.
@MainActor
final class BackendRunner {
    private var activeProcess: Process?

    func cancel() {
        if let activeProcess, activeProcess.isRunning { activeProcess.terminate() }
    }

    func run<Value: Decodable & Sendable>(
        _ type: Value.Type,
        arguments: [String],
        input: Data? = nil,
        onProgress: @MainActor (String, String) -> Void = { _, _ in }
    ) async throws -> Value {
        guard activeProcess == nil else { throw InstallerError(message: "另一項作業尚未結束。") }
        let resources = Bundle.main.resourceURL ?? Bundle(for: BackendRunner.self).resourceURL
        let script = resources?.appending(path: "resign_cli.py")
        let python = resources?.appending(path: "Runtime/bin/python3")
        guard let script, let python, FileManager.default.isExecutableFile(atPath: python.path) else {
            throw InstallerError(message: "找不到本機安裝引擎，請重新建置程式。")
        }
        let process = Process()
        let pipe = Pipe()
        let errorURL = URL.temporaryDirectory.appending(path: "sideload-stderr-\(UUID().uuidString)")
        FileManager.default.createFile(atPath: errorURL.path, contents: nil)
        let errorFile = try FileHandle(forWritingTo: errorURL)
        process.executableURL = python
        process.arguments = ["-I", "-B", "-u", script.path, "--json"] + arguments
        process.standardOutput = pipe
        process.standardError = errorFile
        let inputPipe = input == nil ? nil : Pipe()
        if let inputPipe { process.standardInput = inputPipe }
        else { process.standardInput = FileHandle.nullDevice }
        // Avoid inheriting Python search-path overrides into the bundled engine.
        var environment = ProcessInfo.processInfo.environment
        environment.removeValue(forKey: "PYTHONPATH")
        environment.removeValue(forKey: "PYTHONHOME")
        // Xcode's injected test libraries belong to the Swift host, not Python.
        for key in environment.keys.filter({ $0.hasPrefix("DYLD_") }) {
            environment.removeValue(forKey: key)
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment
        let exits = AsyncStream<Int32> { continuation in
            process.terminationHandler = { terminated in
                continuation.yield(terminated.terminationStatus)
                continuation.finish()
            }
        }
        activeProcess = process
        defer {
            activeProcess = nil
            try? errorFile.close()
            try? FileManager.default.removeItem(at: errorURL)
        }
        try process.run()
        if let inputPipe, let input {
            do {
                try inputPipe.fileHandleForWriting.write(contentsOf: input)
                try inputPipe.fileHandleForWriting.close()
            } catch {
                cancel()
                throw InstallerError(message: "無法傳遞本次作業的密碼。")
            }
        }
        var result: Value?
        var reportedError: String?
        do {
            for try await line in pipe.fileHandleForReading.bytes.lines {
                guard let event = try? JSONDecoder().decode(BackendEvent<Value>.self, from: Data(line.utf8)) else { continue }
                switch event.type {
                case "result": result = event.data
                case "error": reportedError = event.message
                case "progress": onProgress(event.message ?? "處理中…", event.stage ?? "inspect")
                default: break
                }
            }
        } catch {
            cancel()
            throw error
        }
        var status: Int32 = -1
        for await code in exits { status = code; break }
        if let reportedError { throw InstallerError(message: reportedError) }
        guard status == 0, let result else {
            let detail = (try? String(contentsOf: errorURL, encoding: .utf8)) ?? ""
            throw InstallerError(message: "本機工具未完成作業（\(status)）。請確認使用完整的 App 發佈包。\n\(detail.suffix(6000))")
        }
        return result
    }
}
