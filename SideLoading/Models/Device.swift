import Foundation

struct Device: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let model: String
    let osVersion: String
    let available: Bool
    let connected: Bool
    let developerMode: String
    let transport: String

    var status: String {
        if connected { "已連線" }
        else if available { "已配對，待連線" }
        else { "尚未連線" }
    }
}
