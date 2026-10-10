// mitra-stt: Apple's on-device speech recogniser (the Siri/Dictation engine)
// as a tiny HTTP service on the Mac Mini, for the Pi5's stt_node.
//
//   POST /stt?lang=en-IN&context=Mitra,Hey%20Mitra    body = one WAV utterance
//     -> 200 {"transcript": "...", "secs": 0.31, "on_device": true}
//   GET  /health -> 200 {"ok": true, "auth": "authorized", "locales": [...]}
//
// Same shape as Sarvam's REST call (one whole utterance in, text out), so the
// Pi side is a 40-line provider: pi5_voice_pkg/stt_providers/apple.py.
// On-device only by default (no audio leaves the Mac); ?server=1 lets Apple's
// servers help when the on-device model is missing for that locale.
// No Telugu: SFSpeechRecognizer has no te-IN at all (probed 2026-10-10,
// macOS 15.5) -- en-IN and hi-IN only for India, hi-IN without an on-device model.
//
// Build + run: scripts/mac_mini/stt_server/build.sh, then ./mitra-stt [port]

import Foundation
import Network
@preconcurrency import Speech

let port = UInt16(CommandLine.arguments.dropFirst().first ?? "") ?? 8091
let defaultLocale = "en-IN"
let timeoutS = 15.0

// MARK: - recognition

enum STTError: Error { case badLocale(String), unavailable(String), failed(String), timeout }

/// One recogniser per locale, built once (building one costs ~50 ms).
final class Recognizers: @unchecked Sendable {
    private var cache: [String: SFSpeechRecognizer] = [:]
    private let lock = NSLock()
    func get(_ id: String) throws -> SFSpeechRecognizer {
        lock.lock(); defer { lock.unlock() }
        if let r = cache[id] { return r }
        guard let r = SFSpeechRecognizer(locale: Locale(identifier: id)) else { throw STTError.badLocale(id) }
        r.defaultTaskHint = .dictation
        cache[id] = r
        return r
    }
}
let recognizers = Recognizers()

/// Resumes a continuation exactly once (the result handler fires many times).
final class Once<T: Sendable>: @unchecked Sendable {
    private var done = false
    private let lock = NSLock()
    private let cont: CheckedContinuation<T, Error>
    init(_ c: CheckedContinuation<T, Error>) { cont = c }
    func finish(_ r: Result<T, Error>) {
        lock.lock(); defer { lock.unlock() }
        if done { return }
        done = true
        cont.resume(with: r)
    }
}

func transcribe(wav: Data, locale: String, context: [String], allowServer: Bool) async throws -> (String, Bool) {
    let rec = try recognizers.get(locale)
    guard rec.isAvailable else { throw STTError.unavailable("recogniser for \(locale) not available") }
    let onDevice = rec.supportsOnDeviceRecognition
    if !onDevice && !allowServer {
        throw STTError.unavailable("no on-device model for \(locale) (pass server=1 to allow Apple's servers)")
    }
    let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("mitra-stt-\(UUID().uuidString).wav")
    try wav.write(to: url)
    defer { try? FileManager.default.removeItem(at: url) }

    let req = SFSpeechURLRecognitionRequest(url: url)
    req.requiresOnDeviceRecognition = onDevice
    req.shouldReportPartialResults = false
    req.addsPunctuation = true
    req.taskHint = .dictation
    if !context.isEmpty { req.contextualStrings = context }   // "Mitra" instead of "Metro"

    let text: String = try await withCheckedThrowingContinuation { cont in
        let once = Once(cont)
        var task: SFSpeechRecognitionTask?
        task = rec.recognitionTask(with: req) { result, error in
            if let result, result.isFinal {
                once.finish(.success(result.bestTranscription.formattedString))
            } else if let error = error as NSError? {
                // 1110 = "No speech detected": a normal empty result, not a failure
                // (stt_providers/base.py: ProviderUnavailable is for real faults only).
                if error.domain == "kAFAssistantErrorDomain" && error.code == 1110 {
                    once.finish(.success(""))
                } else {
                    once.finish(.failure(STTError.failed("\(error.domain) \(error.code): \(error.localizedDescription)")))
                }
            }
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeoutS) {
            task?.cancel()
            once.finish(.failure(STTError.timeout))
        }
    }
    return (text.trimmingCharacters(in: .whitespacesAndNewlines), onDevice)
}

// MARK: - HTTP (just enough of HTTP/1.1 for one request per connection)

func json(_ obj: Any) -> Data { (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8) }

func respond(_ conn: NWConnection, _ status: Int, _ body: Data) {
    let reason = [200: "OK", 400: "Bad Request", 404: "Not Found", 503: "Service Unavailable"][status] ?? "Error"
    var head = "HTTP/1.1 \(status) \(reason)\r\nContent-Type: application/json\r\n"
    head += "Content-Length: \(body.count)\r\nConnection: close\r\n\r\n"
    conn.send(content: Data(head.utf8) + body, completion: .contentProcessed { _ in conn.cancel() })
}

func authName() -> String {
    switch SFSpeechRecognizer.authorizationStatus() {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "notDetermined"
    @unknown default: return "unknown"
    }
}

func handle(_ conn: NWConnection, method: String, target: String, body: Data) {
    let comps = URLComponents(string: target)
    let q = Dictionary((comps?.queryItems ?? []).map { ($0.name, $0.value ?? "") }, uniquingKeysWith: { $1 })
    switch (method, comps?.path ?? "") {
    case ("GET", "/health"):
        let ids = ["en-IN", "en-US", "en-GB", "hi-IN"]
        let locales = ids.map { id -> [String: Any] in
            let r = try? recognizers.get(id)
            return ["id": id, "available": r?.isAvailable ?? false, "on_device": r?.supportsOnDeviceRecognition ?? false]
        }
        let ok = SFSpeechRecognizer.authorizationStatus() == .authorized
        respond(conn, ok ? 200 : 503, json(["ok": ok, "auth": authName(), "locales": locales]))
    case ("POST", "/stt"):
        guard !body.isEmpty else { return respond(conn, 400, json(["error": "empty body (send a WAV)"])) }
        let locale = q["lang"].flatMap { $0.isEmpty ? nil : $0 } ?? defaultLocale
        let context = (q["context"] ?? "").split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        let allowServer = q["server"] == "1"
        let t0 = Date()
        Task {
            do {
                let (text, onDevice) = try await transcribe(wav: body, locale: locale, context: context, allowServer: allowServer)
                let secs = Date().timeIntervalSince(t0)
                print(String(format: "%.2fs %@ %@", secs, locale, text)); fflush(stdout)
                respond(conn, 200, json(["transcript": text, "secs": secs, "on_device": onDevice]))
            } catch {
                print("FAIL \(locale): \(error)"); fflush(stdout)
                respond(conn, 503, json(["error": "\(error)"]))
            }
        }
    default:
        respond(conn, 404, json(["error": "POST /stt or GET /health"]))
    }
}

/// Reads until the headers and Content-Length bytes of body have arrived.
func receive(_ conn: NWConnection, _ buf: Data = Data()) {
    conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { data, _, isComplete, error in
        var buf = buf
        if let data { buf.append(data) }
        if let sep = buf.range(of: Data("\r\n\r\n".utf8)) {
            let head = String(decoding: buf[..<sep.lowerBound], as: UTF8.self)
            let lines = head.components(separatedBy: "\r\n")
            let parts = lines.first?.split(separator: " ").map(String.init) ?? []
            var length = 0
            for l in lines.dropFirst() where l.lowercased().hasPrefix("content-length:") {
                length = Int(l.split(separator: ":")[1].trimmingCharacters(in: .whitespaces)) ?? 0
            }
            let body = buf[sep.upperBound...]
            if parts.count >= 2 && body.count >= length {
                return handle(conn, method: parts[0], target: parts[1], body: Data(body.prefix(length)))
            }
        }
        if isComplete || error != nil || buf.count > 32 << 20 { return conn.cancel() }
        receive(conn, buf)
    }
}

// MARK: - main

print("mitra-stt: speech recognition auth = \(authName())")
if SFSpeechRecognizer.authorizationStatus() == .notDetermined {
    print("mitra-stt: asking for Speech Recognition permission -- click Allow on the Mac's screen")
    SFSpeechRecognizer.requestAuthorization { _ in print("mitra-stt: auth now \(authName())"); fflush(stdout) }
}
let listener = try NWListener(using: .tcp, on: NWEndpoint.Port(rawValue: port)!)
listener.newConnectionHandler = { conn in
    conn.start(queue: .global())
    receive(conn)
}
listener.stateUpdateHandler = { if case .failed(let e) = $0 { print("listener failed: \(e)"); exit(1) } }
listener.start(queue: .main)
print("mitra-stt: listening on :\(port) (default \(defaultLocale), on-device)"); fflush(stdout)
dispatchMain()
