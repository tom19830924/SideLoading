import Foundation

struct CapabilityOption: Decodable, Identifiable, Sendable {
    let id: String
    let title: String
    let impact: String
    let bundles: [String]
    var isOmitted = false

    private enum CodingKeys: String, CodingKey {
        case id, title, impact, bundles
    }
}
