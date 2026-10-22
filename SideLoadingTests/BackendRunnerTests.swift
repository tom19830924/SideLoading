import Foundation
import Testing
@testable import SideLoading

@MainActor
struct BackendRunnerTests {
    @Test func reportsStructuredErrorsAndCanRunAgain() async throws {
        let runner = BackendRunner()
        for _ in 0..<2 {
            do {
                _ = try await runner.run(IPAInfo.self, arguments: ["inspect", "/tmp/nonexistent-sideload-test.ipa"])
                Issue.record("Expected invalid file error")
            } catch let error as InstallerError {
                #expect(error.message.contains("請選擇有效的 .ipa"))
            }
        }
    }

    @Test func readsFixtureThroughNativeProcessBridge() async throws {
        let folder = URL.temporaryDirectory.appending(path: "sideload-swift-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let fixture = folder.appending(path: "Fixture.ipa")
        let script = """
        import plistlib, subprocess, sys, zipfile
        from pathlib import Path
        folder = Path(sys.argv[1]).parent
        source = folder / 'main.c'
        binary = folder / 'Test'
        source.write_text('int main(void) { return 0; }')
        sdk = subprocess.check_output(['/usr/bin/xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
        subprocess.run(['/usr/bin/xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, str(source), '-o', str(binary)], check=True)
        with zipfile.ZipFile(sys.argv[1], 'w') as z:
            z.writestr('Payload/Test.app/Info.plist', plistlib.dumps({'CFBundleIdentifier':'test.fixture','CFBundleExecutable':'Test','CFBundleDisplayName':'測試 App','MinimumOSVersion':'17.0'}))
            z.write(binary, 'Payload/Test.app/Test')
        """
        let process = Process()
        process.executableURL = URL(filePath: "/usr/bin/xcrun")
        process.arguments = ["python3", "-I", "-c", script, fixture.path]
        try process.run()
        process.waitUntilExit()
        #expect(process.terminationStatus == 0)
        let result = try await BackendRunner().run(IPAInfo.self, arguments: ["inspect", fixture.path])
        #expect(result.name == "測試 App")
        #expect(result.bundleID == "test.fixture")
        #expect(!result.encrypted)
        #expect(result.extensions?.isEmpty == true)
        #expect(result.healthRecordsRequested == false)
    }
}
