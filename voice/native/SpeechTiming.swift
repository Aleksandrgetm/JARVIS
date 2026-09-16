import Foundation

// Pure, monotonic timing policy, independently testable without microphone/Speech.
struct SpeechTiming {
    enum Boundary: Equatable { case waiting, noSpeech, endSilence, maxDuration }
    let startTimeout: Double
    let maxDuration: Double
    let endSilence: Double
    let started: Double
    var minSpeechDuration: Double = 0.9
    private(set) var speechStarted: Double?
    private(set) var lastVoice: Double?
    private var voicedSince: Double?
    private var voicedBuffers = 0
    private var lastAudio: Double?

    mutating func audio(at now: Double, voiced: Bool) {
        if let previous = lastAudio, now - previous > 0.25 {
            voicedSince = nil
            voicedBuffers = 0
        }
        lastAudio = now
        if voiced {
            if voicedSince == nil { voicedSince = now }
            voicedBuffers += 1
            // Require several consecutive buffers AND 250 ms; one click cannot start speech.
            if speechStarted != nil || (voicedBuffers >= 3 && now - voicedSince! >= 0.25) {
                detected(at: now)
            }
        } else {
            voicedSince = nil
            voicedBuffers = 0
        }
    }

    mutating func detected(at now: Double) {
        if speechStarted == nil { speechStarted = now }
        lastVoice = now
    }

    func boundary(at now: Double) -> Boundary {
        guard let speechStarted = speechStarted else {
            return now - started >= startTimeout ? .noSpeech : .waiting
        }
        if now - speechStarted >= maxDuration { return .maxDuration }
        if now - speechStarted < minSpeechDuration { return .waiting }
        if silenceDuration(at: now) >= endSilence { return .endSilence }
        return .waiting
    }

    func speechDuration(at now: Double) -> Double {
        speechStarted.map { max(0, now - $0) } ?? 0
    }

    func silenceDuration(at now: Double) -> Double {
        // Missing audio is NOT silence. Require incoming buffers through the pause.
        guard let lastVoice = lastVoice, let lastAudio = lastAudio else { return 0 }
        return max(0, min(now, lastAudio) - lastVoice)
    }
}
