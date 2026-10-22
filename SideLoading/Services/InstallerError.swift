import Foundation

struct InstallerError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
