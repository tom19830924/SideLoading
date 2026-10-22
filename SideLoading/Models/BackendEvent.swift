import Foundation

struct BackendEvent<Value: Decodable>: Decodable {
    let type: String
    let message: String?
    let stage: String?
    let data: Value?
}
