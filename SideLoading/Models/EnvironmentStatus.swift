import Foundation

struct EnvironmentStatus: Decodable, Sendable {
    let xcodeReady: Bool
    let devices: [Device]
    let identities: [SigningIdentity]
    let warnings: [String]
}
