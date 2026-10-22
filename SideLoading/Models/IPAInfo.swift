import Foundation

struct IPAInfo: Decodable, Sendable {
    let name: String
    let bundleID: String
    let version: String
    let minimumOS: String
    let size: Int64
    let extensionCount: Int
    let extensions: [ExtensionInfo]?
    let healthRecordsRequested: Bool?
    let optionalCapabilities: [CapabilityOption]?
    let encrypted: Bool
    let fileName: String

    var formattedSize: String {
        ByteCountFormatter.string(fromByteCount: size, countStyle: .file)
    }
}
