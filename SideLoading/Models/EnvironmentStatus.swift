import Foundation

struct EnvironmentStatus: Decodable, Sendable {
    let runtimeReady: Bool
    let devices: [Device]
    let warnings: [String]
}
