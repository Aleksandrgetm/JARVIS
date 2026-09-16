import Foundation

// Serializes append/endAudio across the audio tap and main queue.
// Injected operations let tests verify the real lifecycle without Speech or a microphone.
final class RecognitionLifecycle {
    private let lock = NSLock()
    private var acceptingAudio = true
    private var buffers = 0
    private var seconds = 0.0
    private var inputEnded = false
    private(set) var completed = false
    private var finalDeadline: Double?
    private let stop: () -> Void
    private let end: () -> Void
    private let cancel: () -> Void
    private let trace: (String) -> Void

    init(stop: @escaping () -> Void, end: @escaping () -> Void,
         cancel: @escaping () -> Void, trace: @escaping (String) -> Void) {
        self.stop = stop
        self.end = end
        self.cancel = cancel
        self.trace = trace
    }

    @discardableResult
    func append(frames: Int, sampleRate: Double, operation: () -> Void) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard acceptingAudio, frames > 0, sampleRate > 0 else { return false }
        operation()
        buffers += 1
        seconds += Double(frames) / sampleRate
        return true
    }

    var audioSummary: (buffers: Int, seconds: Double) {
        lock.lock()
        defer { lock.unlock() }
        return (buffers, seconds)
    }

    // Main queue only. Close the gate BEFORE stopping the engine/endAudio.
    private func endInput() {
        guard !inputEnded else { return }
        lock.lock()
        acceptingAudio = false
        lock.unlock()
        inputEnded = true
        stop()
        trace("request.endAudio()")
        end()
    }

    func beginFinalization(at now: Double, timeout: Double) {
        guard finalDeadline == nil && !completed else { return }
        endInput()
        finalDeadline = now + timeout
        trace("waiting for Apple final result")
    }

    func finalTimedOut(at now: Double) -> Bool {
        guard let deadline = finalDeadline, !completed else { return false }
        return now >= deadline
    }

    // No cancel on success: final result means Apple has completed recognition.
    func complete() {
        guard !completed else { return }
        completed = true
        endInput()
    }

    // Cancellation is reserved for errors, timeout or explicit cleanup.
    func abort() {
        guard !completed else { return }
        complete()
        trace("recognitionTask.cancel() (cleanup)")
        cancel()
    }
}

func recognitionErrorStatus(domain: String, code: Int) -> String {
    if domain == "kAFAssistantErrorDomain" && code == 1110 { return "no_speech" }
    return "recognizer_error"
}

// Empty callbacks never erase useful text. A real final always takes precedence.
struct RecognitionTranscript {
    struct Selection {
        let text: String
        let isFallback: Bool
    }

    private(set) var lastNonEmptyTranscription = ""
    private(set) var finalTranscription = ""

    mutating func receive(_ text: String, isFinal: Bool) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        lastNonEmptyTranscription = text
        if isFinal { finalTranscription = text }
    }

    func select(allowFallback: Bool) -> Selection? {
        if !finalTranscription.isEmpty {
            return Selection(text: finalTranscription, isFallback: false)
        }
        if allowFallback && !lastNonEmptyTranscription.isEmpty {
            return Selection(text: lastNonEmptyTranscription, isFallback: true)
        }
        return nil
    }
}
