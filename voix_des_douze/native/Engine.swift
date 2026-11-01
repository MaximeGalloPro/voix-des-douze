// macOS 14+. JSON-lines service; no networking or game automation.
import Foundation
import ScreenCaptureKit
import Vision
import ImageIO
import UniformTypeIdentifiers
import CoreGraphics

struct EngineFailure: Error { let code: String; let message: String }

@main struct Engine {
    static let textRequest: VNRecognizeTextRequest = {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["fr-FR"]
        request.usesLanguageCorrection = true
        return request
    }()

    static func requirePermission() throws {
        if !CGPreflightScreenCaptureAccess() {
            throw EngineFailure(code: "permission", message: "Autorisez l’enregistrement de l’écran dans Réglages Système > Confidentialité et sécurité > Enregistrement de l’écran et de l’audio système, puis relancez l’application.")
        }
    }

    static func execute(_ input: [String: Any]) async throws -> Any {
        switch input["op"] as? String {
        case "permission": return CGPreflightScreenCaptureAccess()
        case "request_permission": return CGRequestScreenCaptureAccess()
        case "windows", "capture":
            try requirePermission()
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
            if input["op"] as? String == "windows" {
                return content.windows.filter { $0.windowLayer == 0 && $0.frame.width > 1 && $0.frame.height > 1 }.map { window -> [String: Any] in
                    let filter = SCContentFilter(desktopIndependentWindow: window)
                    return ["window_id": window.windowID, "title": window.title ?? "", "app": window.owningApplication?.applicationName ?? "", "width": Int(filter.contentRect.width * CGFloat(filter.pointPixelScale)), "height": Int(filter.contentRect.height * CGFloat(filter.pointPixelScale))]
                }
            }
            guard let id = input["window_id"] as? UInt32,
                  let window = content.windows.first(where: { $0.windowID == id }) else {
                throw EngineFailure(code: "closed", message: "La fenêtre sélectionnée n’existe plus. Sélectionnez une nouvelle fenêtre.")
            }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            config.width = Int(filter.contentRect.width * CGFloat(filter.pointPixelScale))
            config.height = Int(filter.contentRect.height * CGFloat(filter.pointPixelScale))
            config.showsCursor = false
            config.ignoreShadowsSingleWindow = true
            config.captureResolution = .best
            let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
            guard let path = input["path"] as? String,
                  let destination = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL, UTType.png.identifier as CFString, 1, nil) else {
                throw EngineFailure(code: "capture", message: "Impossible de créer l’image temporaire.")
            }
            CGImageDestinationAddImage(destination, image, nil)
            guard CGImageDestinationFinalize(destination) else { throw EngineFailure(code: "capture", message: "Échec d’écriture de la capture.") }
            return ["width": image.width, "height": image.height]
        case "ocr":
            guard let path = input["path"] as? String else { throw EngineFailure(code: "ocr", message: "Image OCR manquante.") }
            try VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:]).perform([textRequest])
            return (textRequest.results ?? []).compactMap { observation -> [String: Any]? in
                guard let candidate = observation.topCandidates(1).first else { return nil }
                let box = observation.boundingBox
                return ["text": candidate.string, "confidence": candidate.confidence, "x": box.minX, "y": 1 - box.maxY, "width": box.width, "height": box.height]
            }
        default: throw EngineFailure(code: "protocol", message: "Commande native inconnue.")
        }
    }

    static func main() async {
        while let line = readLine() {
            let output: [String: Any]
            do {
                guard let bytes = line.data(using: .utf8), let input = try JSONSerialization.jsonObject(with: bytes) as? [String: Any] else {
                    throw EngineFailure(code: "protocol", message: "Commande JSON invalide.")
                }
                let result = try await execute(input)
                output = ["result": result]
            } catch let error as EngineFailure {
                output = ["error": error.code, "message": error.message]
            } catch {
                let native = error as NSError
                let streamError = native.domain == SCStreamErrorDomain
                let permission = streamError && native.code == SCStreamError.Code.userDeclined.rawValue
                let closed = streamError && [SCStreamError.Code.noCaptureSource.rawValue, SCStreamError.Code.failedApplicationConnectionInvalid.rawValue, SCStreamError.Code.failedNoMatchingApplicationContext.rawValue].contains(native.code)
                output = ["error": permission ? "permission" : (closed ? "closed" : "native"), "message": error.localizedDescription]
            }
            if let data = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]), let string = String(data: data, encoding: .utf8) {
                print(string)
                fflush(stdout)
            }
        }
    }
}
