import Foundation

struct ExtensionInfo: Decodable, Identifiable, Sendable {
    let name: String
    let bundleID: String
    var id: String { bundleID }
}
