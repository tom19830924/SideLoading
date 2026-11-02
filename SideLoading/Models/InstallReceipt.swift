import Foundation

struct InstallReceipt: Codable, Identifiable, Sendable {
    var id: String { bundleID + deviceName }
    let name: String
    let bundleID: String
    let deviceName: String
    let expires: String
    let output: String

    var expirationDate: Date? { ISO8601DateFormatter().date(from: expires) }
}
