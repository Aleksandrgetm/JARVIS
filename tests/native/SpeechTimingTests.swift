import Foundation

// Deterministic native policy tests. No microphone, Speech services or real waits.
@main
struct SpeechTimingTests {
    static func main() {
        func fresh() -> SpeechTiming {
            SpeechTiming(startTimeout: 7, maxDuration: 15, endSilence: 1.8, started: 0)
        }
        var timing = fresh()
        precondition(timing.boundary(at: 0.1) == .waiting, "must not end at startup")
        precondition(timing.boundary(at: 6.99) == .waiting, "must wait seven seconds")
        precondition(timing.boundary(at: 7) == .noSpeech, "silence must time out")

        timing.audio(at: 6, voiced: true)
        timing.audio(at: 6.1, voiced: true)
        timing.audio(at: 6.2, voiced: true)
        timing.audio(at: 6.3, voiced: true)
        precondition(timing.speechStarted != nil, "audio detects onset")
        for index in 64...212 {
            let now = Double(index) / 10
            timing.audio(at: now, voiced: true)
            precondition(timing.boundary(at: now) == .waiting, "continuous speech must not be cut at 1-2s or total 15s")
        }
        precondition(timing.boundary(at: 21.31) == .maxDuration, "hard limit measured from onset")

        timing = fresh()
        timing.detected(at: 1)
        timing.audio(at: 4, voiced: true)
        timing.audio(at: 5.79, voiced: false)
        precondition(timing.boundary(at: 5.79) == .waiting, "short pause is allowed")
        timing.audio(at: 5.81, voiced: false)
        precondition(timing.boundary(at: 5.81) == .endSilence, "silence ends the utterance")

        timing = fresh()
        timing.audio(at: 1, voiced: true)
        timing.audio(at: 1.05, voiced: false)
        precondition(timing.speechStarted == nil, "a click is not speech")
        precondition(timing.boundary(at: 7) == .noSpeech, "a click must not extend the deadline")

        timing = fresh()
        timing.detected(at: 6.9)
        precondition(timing.boundary(at: 7.1) == .waiting, "quiet recognized speech gets its own utterance window")
        timing = SpeechTiming(startTimeout: 7, maxDuration: 15, endSilence: 0.3,
                              started: 0, minSpeechDuration: 0.9)
        timing.detected(at: 1)
        timing.audio(at: 1.5, voiced: false)
        precondition(timing.boundary(at: 1.5) == .waiting, "minimum speech duration protects a short pause")
        precondition(timing.boundary(at: 1.89) == .waiting, "must not finish before minimum")
        timing.audio(at: 1.91, voiced: false)
        precondition(timing.boundary(at: 1.91) == .endSilence, "can finish after minimum and silence")

        timing = fresh()
        timing.audio(at: 1, voiced: true)
        timing.audio(at: 1.6, voiced: true)
        precondition(timing.speechStarted == nil, "isolated loud buffers cannot confirm onset")
        timing.audio(at: 1.65, voiced: true)
        timing.audio(at: 1.7, voiced: true)
        precondition(timing.speechStarted == nil, "multiple short clicks still need sustained duration")
        timing.audio(at: 1.86, voiced: true)
        precondition(timing.speechStarted != nil, "sustained input confirms onset")

        timing = fresh()
        timing.detected(at: 1)
        timing.audio(at: 1.1, voiced: true)
        precondition(timing.boundary(at: 4) == .waiting, "lost audio buffers are not end silence")

        timing = SpeechTiming(startTimeout: 7, maxDuration: 15, endSilence: 0.9, started: 0)
        timing.detected(at: 1)
        timing.audio(at: 2, voiced: true)
        timing.audio(at: 2.8, voiced: false)
        precondition(timing.boundary(at: 2.8) == .waiting, "0.8s pause must not end a 0.9s window")
        timing.audio(at: 2.85, voiced: true)
        timing.audio(at: 3.6, voiced: false)
        precondition(timing.boundary(at: 3.6) == .waiting, "resumed speech resets silence")
        timing.audio(at: 3.76, voiced: false)
        precondition(timing.boundary(at: 3.76) == .endSilence, "0.9s end silence is configurable")

        timing = SpeechTiming(startTimeout: 7, maxDuration: 15, endSilence: 0.7, started: 0)
        timing.detected(at: 1)
        timing.audio(at: 2, voiced: true)
        timing.audio(at: 2.65, voiced: false)
        precondition(timing.boundary(at: 2.65) == .waiting, "0.65s pause stays inside 0.7s window")
        timing.audio(at: 2.71, voiced: false)
        precondition(timing.boundary(at: 2.71) == .endSilence, "0.7s window ends after silence")

        testRecognitionLifecycle()
        testTranscriptionFallback()
        print("SpeechTiming + RecognitionLifecycle + transcription A–E: all native tests passed")
    }

    static func testRecognitionLifecycle() {
        var calls: [String] = []
        let lifecycle = RecognitionLifecycle(stop: { calls.append("stop") },
            end: { calls.append("endAudio") }, cancel: { calls.append("cancel") }, trace: { _ in })
        precondition(lifecycle.append(frames: 4410, sampleRate: 44100) { calls.append("append") })
        precondition(lifecycle.audioSummary.buffers == 1)
        precondition(abs(lifecycle.audioSummary.seconds - 0.1) < 0.0001)
        lifecycle.beginFinalization(at: 10, timeout: 5)
        precondition(calls == ["append", "stop", "endAudio"], "normal silence ends input without cancel")
        precondition(!lifecycle.completed, "must wait for Apple final result")
        precondition(!lifecycle.finalTimedOut(at: 14.99), "must allow full finalization window")
        precondition(!lifecycle.append(frames: 4410, sampleRate: 44100) { calls.append("late append") },
                     "no append after endAudio")
        lifecycle.beginFinalization(at: 11, timeout: 5)
        precondition(calls == ["append", "stop", "endAudio"], "endAudio exactly once")
        lifecycle.complete()
        lifecycle.complete()
        lifecycle.abort()
        precondition(!calls.contains("cancel"), "successful final result must never be cancelled")
        precondition(!lifecycle.finalTimedOut(at: 16), "completed result disables watchdog")

        calls = []
        let timeout = RecognitionLifecycle(stop: { calls.append("stop") },
            end: { calls.append("endAudio") }, cancel: { calls.append("cancel") }, trace: { _ in })
        timeout.beginFinalization(at: 20, timeout: 5)
        precondition(timeout.finalTimedOut(at: 25), "timeout enables cleanup")
        timeout.abort()
        timeout.abort()
        precondition(calls == ["stop", "endAudio", "cancel"], "timeout cancels exactly once after endAudio")

        calls = []
        let failure = RecognitionLifecycle(stop: { calls.append("stop") },
            end: { calls.append("endAudio") }, cancel: { calls.append("cancel") }, trace: { _ in })
        failure.abort()
        precondition(calls == ["stop", "endAudio", "cancel"], "error cleanup releases recording and request")
        precondition(!failure.append(frames: 4410, sampleRate: 44100) { calls.append("late append") })
        precondition(recognitionErrorStatus(domain: "kAFAssistantErrorDomain", code: 1110) == "no_speech")
        precondition(recognitionErrorStatus(domain: "kAFAssistantErrorDomain", code: 1101) == "recognizer_error")
        precondition(recognitionErrorStatus(domain: "OtherDomain", code: 1110) == "recognizer_error")
    }

    static func testTranscriptionFallback() {
        let full = "Джарвис открой Safari"
        // A: empty final preserves a valid partial, but only after the grace period.
        var a = RecognitionTranscript()
        a.receive(full, isFinal: false)
        a.receive("", isFinal: true)
        precondition(a.select(allowFallback: false) == nil)
        precondition(a.select(allowFallback: true)?.text == full)
        precondition(a.select(allowFallback: true)?.isFallback == true)
        // B: no non-empty result means recognition failure.
        var b = RecognitionTranscript()
        b.receive("", isFinal: false)
        b.receive("", isFinal: true)
        precondition(b.select(allowFallback: true) == nil)
        // C: retain the newest non-empty hypothesis.
        var c = RecognitionTranscript()
        c.receive("Джарвис", isFinal: false)
        c.receive(full, isFinal: false)
        c.receive("", isFinal: true)
        precondition(c.select(allowFallback: true)?.text == full)
        // D: real final has priority and never takes the fallback path.
        var d = RecognitionTranscript()
        d.receive(full, isFinal: false)
        d.receive(full, isFinal: true)
        precondition(d.select(allowFallback: false)?.text == full)
        precondition(d.select(allowFallback: true)?.isFallback == false)
        d.receive("Открой Safari", isFinal: true)
        d.receive(full, isFinal: false)
        precondition(d.select(allowFallback: true)?.text == "Открой Safari")
        // E: empty callbacks, including whitespace, cannot erase retained text.
        var e = RecognitionTranscript()
        e.receive(full, isFinal: false)
        e.receive(" \n ", isFinal: false)
        e.receive("", isFinal: true)
        precondition(e.lastNonEmptyTranscription == full)
        // Missing final: wait for the deadline, save result, THEN cancel for cleanup.
        var calls: [String] = []
        let lifecycle = RecognitionLifecycle(stop: { calls.append("stop") },
            end: { calls.append("endAudio") }, cancel: {
                calls.append("cancel")
                e.receive("", isFinal: true)
            }, trace: { _ in })
        lifecycle.beginFinalization(at: 10, timeout: 2.5)
        precondition(!lifecycle.finalTimedOut(at: 12.49))
        precondition(lifecycle.finalTimedOut(at: 12.5))
        let savedResult = e.select(allowFallback: true)!
        calls.append("saved")
        lifecycle.abort()
        precondition(calls == ["stop", "endAudio", "saved", "cancel"])
        precondition(savedResult.text == full)
    }
}
