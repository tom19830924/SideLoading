import Foundation
import Testing
@testable import SideLoading

@MainActor
struct InstallerModelTests {
    @Test func requiresIPADeviceCertificateAndProfileWithoutXcodeIdentity() {
        let model = InstallerModel()
        model.environment = EnvironmentStatus(runtimeReady: true, devices: [
            Device(id: "DEVICE", name: "Fixture", model: "iPhone", osVersion: "26.0", available: true,
                   connected: true, developerMode: "enabled", transport: "USB")], warnings: [])
        #expect(!model.canInstall)
        model.ipa = IPAInfo(name: "Fixture", bundleID: "example.fixture", version: "1.0", minimumOS: "17.0", size: 100,
                           extensionCount: 0, extensions: [], healthRecordsRequested: false, optionalCapabilities: [],
                           encrypted: false, fileName: "Fixture.ipa")
        model.ipaURL = URL(filePath: "/tmp/Fixture.ipa")
        model.selectedDeviceID = "DEVICE"
        #expect(!model.canInstall)
        model.certificateURL = URL(filePath: "/tmp/Fixture.p12")
        #expect(!model.canInstall)
        model.profileURL = URL(filePath: "/tmp/Fixture.mobileprovision")
        #expect(model.canInstall)
        model.isBusy = true
        #expect(!model.canInstall)
    }

    @Test func reimportClearsExtensionProfilesAndReportsInvalidIPA() async {
        let model = InstallerModel()
        model.extensionProfiles = ["example.widget": URL(filePath: "/tmp/Widget.mobileprovision")]
        await model.importIPA(URL(filePath: "/tmp/nonexistent-model-test.ipa"))
        #expect(model.extensionProfiles.isEmpty)
        #expect(model.ipa == nil)
        #expect(model.errorMessage?.contains("請選擇有效的 .ipa") == true)
        #expect(!model.isBusy)
    }
}
