import Foundation

struct SigningIdentity: Decodable, Identifiable, Sendable {
    let id: String
    let name: String
    let teamID: String
}
