import AVFoundation
import Foundation
import Speech

func emit(_ payload: [String: Any]) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
    exit(0)
}

// One invocation recognizes at most one utterance and then releases the device.
final class SpeechSession {
    private let microphone = MicrophoneCapture()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var lifecycle: RecognitionLifecycle?
    private var timer: Timer?
    private var timing: SpeechTiming
    private var transcript = RecognitionTranscript()
    private var perf: [String: Double] = [:]
    private let wallOffset = Date().timeIntervalSince1970 - ProcessInfo.processInfo.systemUptime
    private var ending = false
    private var finished = false
    private var microphoneRunning = false
    private var bufferCount = 0
    private var lastBufferAt: Double?
    private var peakDB: Float = -140
    private var currentDB: Float = -140
    private var lastDiagnostic = 0.0
    private let recognizer: SFSpeechRecognizer
    private let finalTimeout: Double
    private let allowNetwork: Bool
    private let debug: Bool

    init(recognizer: SFSpeechRecognizer, timeout: Double, allowNetwork: Bool,
         startTimeout: Double, endSilence: Double, minSpeechDuration: Double,
         finalTimeout: Double, debug: Bool) {
        self.recognizer = recognizer
        self.allowNetwork = allowNetwork
        self.finalTimeout = finalTimeout
        self.debug = debug
        self.timing = SpeechTiming(startTimeout: startTimeout, maxDuration: timeout,
                                   endSilence: endSilence, started: ProcessInfo.processInfo.systemUptime,
                                   minSpeechDuration: minSpeechDuration)
    }

    private func trace(_ message: String) {
        guard debug else { return }
        FileHandle.standardError.write(Data(("[VOICE] " + message + "\n").utf8))
    }

    private func mark(_ name: String, at uptime: Double = ProcessInfo.processInfo.systemUptime) {
        guard perf[name] == nil else { return }
        let timestamp = wallOffset + uptime
        perf[name] = timestamp
        if debug {
            FileHandle.standardError.write(Data(("[PERF] \(name) timestamp=\(String(format: "%.6f", timestamp))\n").utf8))
        }
    }

    private func traceError(_ error: NSError) {
        let message = error.localizedDescription.components(separatedBy: .newlines).joined(separator: " ")
        trace("error domain=\(error.domain) code=\(error.code) message=\(message.prefix(240))")
    }

    private func stopMicrophone() {
        guard microphoneRunning else { return }
        microphone.stop()
        microphoneRunning = false
        let summary = lifecycle?.audioSummary
        trace("microphone stopped; appended_buffers=\(summary?.buffers ?? 0) audio_seconds=\(String(format: "%.2f", summary?.seconds ?? 0)) peak_dBFS=\(String(format: "%.1f", peakDB))")
    }

    func start() {
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        request.requiresOnDeviceRecognition = !allowNetwork
        self.request = request
        trace("locale=\(recognizer.locale.identifier)")
        trace("supportsOnDeviceRecognition=\(recognizer.supportsOnDeviceRecognition)")
        trace("requiresOnDeviceRecognition=\(request.requiresOnDeviceRecognition)")
        let lifecycle = RecognitionLifecycle(
            stop: { [weak self] in self?.stopMicrophone() },
            end: { request.endAudio() },
            cancel: { [weak self] in self?.task?.cancel() },
            trace: { [weak self] message in self?.trace(message) })
        self.lifecycle = lifecycle
        // Register the Speech consumer before the tap starts delivering native PCM buffers.
        task = recognizer.recognitionTask(with: request) { result, error in
            DispatchQueue.main.async {
                guard !self.finished else { return }
                if let result = result {
                    let text = result.bestTranscription.formattedString.trimmingCharacters(in: .whitespacesAndNewlines)
                    let previous = self.transcript.lastNonEmptyTranscription
                    self.transcript.receive(text, isFinal: result.isFinal)
                    if !text.isEmpty && text != previous {
                        let wasWaiting = self.timing.speechStarted == nil
                        // Recognition is a secondary onset signal for very quiet speech.
                        self.timing.detected(at: ProcessInfo.processInfo.systemUptime)
                        if wasWaiting { self.trace("speech detected"); self.mark("speech_start") }
                    }
                    if result.isFinal {
                        self.trace("final: " + text)
                        if self.ending && !text.isEmpty {
                            self.completeFinal(allowFallback: false)
                            return
                        }
                    } else {
                        self.trace("partial: " + text)
                    }
                }
                if let error = error as NSError? {
                    self.traceError(error)
                    let status = recognitionErrorStatus(domain: error.domain, code: error.code)
                    if self.ending && status == "no_speech" && !self.transcript.lastNonEmptyTranscription.isEmpty {
                        // Apple may report 1110 after endAudio despite useful partials.
                        // Keep the saved text and the existing finalization deadline.
                        return
                    }
                    self.finish(["status": status, "error_domain": error.domain, "error_code": error.code])
                }
            }
        }
        do {
            try microphone.start { buffer, level in
                guard lifecycle.append(frames: Int(buffer.frameLength), sampleRate: buffer.format.sampleRate,
                                       operation: { request.append(buffer) }) else { return }
                let capturedAt = ProcessInfo.processInfo.systemUptime
                let sampleRate = buffer.format.sampleRate
                let pcm = buffer.format.commonFormat == .pcmFormatFloat32 ? "Float32" : "PCM(\(buffer.format.commonFormat.rawValue))"
                let formatDescription = "sample_rate=\(sampleRate) channels=\(buffer.format.channelCount) format=\(pcm) interleaved=\(buffer.format.isInterleaved) frames=\(buffer.frameLength)"
                DispatchQueue.main.async {
                    guard !self.finished && !self.ending else { return }
                    self.bufferCount += 1
                    self.lastBufferAt = capturedAt
                    self.peakDB = max(self.peakDB, level)
                    self.currentDB = level
                    if self.bufferCount == 1 {
                        self.trace("audio buffer appended; " + formatDescription)
                    }
                    let wasWaiting = self.timing.speechStarted == nil
                    self.timing.audio(at: capturedAt, voiced: level >= -45)
                    if wasWaiting && self.timing.speechStarted != nil {
                        self.trace("speech detected")
                        self.mark("speech_start", at: capturedAt)
                    }
                }
            }
            microphoneRunning = true
            // Start waiting only once AVAudioEngine has started successfully.
            timing = SpeechTiming(startTimeout: timing.startTimeout, maxDuration: timing.maxDuration,
                                  endSilence: timing.endSilence, started: ProcessInfo.processInfo.systemUptime,
                                  minSpeechDuration: timing.minSpeechDuration)
            trace("microphone started")
            trace("waiting for speech; start_timeout=\(timing.startTimeout)s max_utterance=\(timing.maxDuration)s min_speech=\(timing.minSpeechDuration)s end_silence=\(timing.endSilence)s")
        } catch {
            traceError(error as NSError)
            finish(["status": "microphone_unavailable"])
            return
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            guard !self.ending && !self.finished else { return }
            let now = ProcessInfo.processInfo.systemUptime
            if self.debug && now - self.lastDiagnostic >= 1 {
                self.lastDiagnostic = now
                let summary = lifecycle.audioSummary
                self.trace("buffer count=\(summary.buffers) appended_audio=\(String(format: "%.2f", summary.seconds))s current_dBFS=\(String(format: "%.1f", self.currentDB)) speech duration=\(String(format: "%.2f", self.timing.speechDuration(at: now)))s silence duration=\(String(format: "%.2f", self.timing.silenceDuration(at: now)))s")
            }
            // Distinguish a dead input engine from a working microphone receiving silence.
            if now - (self.lastBufferAt ?? self.timing.started) >= 2 {
                self.trace("audio buffer watchdog: no buffers for 2s")
                self.finish(["status": "audio_unavailable"])
                return
            }
            switch self.timing.boundary(at: now) {
            case .waiting: break
            case .noSpeech:
                self.trace("start speech timeout")
                self.finish(["status": "no_speech"])
            case .endSilence, .maxDuration:
                self.trace(self.timing.boundary(at: now) == .maxDuration ? "maximum utterance duration" : "end silence detected")
                if let lastVoice = self.timing.lastVoice { self.mark("speech_end_estimated", at: lastVoice) }
                self.mark("speech_end_detected", at: now)
                self.ending = true
                lifecycle.beginFinalization(at: now, timeout: self.finalTimeout)
                if self.transcript.select(allowFallback: false) != nil {
                    self.completeFinal(allowFallback: false)
                    return
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + self.finalTimeout) {
                    if !self.finished && lifecycle.finalTimedOut(at: ProcessInfo.processInfo.systemUptime) {
                        self.trace("final result not emitted by Apple (non-empty final unavailable)")
                        self.completeFinal(allowFallback: true)
                    }
                }
            }
        }
    }

    private func completeFinal(allowFallback: Bool) {
        guard let result = transcript.select(allowFallback: allowFallback) else {
            finish(["status": "recognition_failed"])
            return
        }
        if result.isFallback { trace("using transcription fallback: " + result.text) }
        // Copy the selected text into the result BEFORE cleanup can cancel the task.
        let payload: [String: Any] = ["status": "ok", "text": result.text,
                                      "source": result.isFallback ? "fallback" : "final"]
        finish(payload)
    }

    private func finish(_ payload: [String: Any]) {
        guard !finished else { return }
        finished = true
        timer?.invalidate()
        if payload["status"] as? String == "ok" && payload["source"] as? String != "fallback" {
            lifecycle?.complete()
        } else {
            lifecycle?.abort()
        }
        mark("stt_native_final")
        var output = payload
        output["perf"] = perf
        emit(output)
    }
}

@main
struct SpeechBridge {
    static var session: SpeechSession?

    static func main() {
        let args = CommandLine.arguments
        guard [5, 9, 10].contains(args.count), ["--listen", "--authorize", "--check"].contains(args[1]),
              let timeout = Double(args[3]), timeout.isFinite, timeout >= 1, timeout <= 30,
              ["local", "network"].contains(args[4]) else {
            emit(["status": "invalid_configuration"])
        }
        let startTimeout = args.count >= 9 ? Double(args[5]) ?? -1 : 7
        let endSilence = args.count >= 9 ? Double(args[6]) ?? -1 : 0.7
        let finalTimeout = args.count >= 9 ? Double(args[7]) ?? -1 : 2.5
        let debug = args.count >= 9 && args[8] == "debug"
        let minSpeechDuration = args.count == 10 ? Double(args[9]) ?? -1 : 0.9
        guard startTimeout.isFinite && (1...30).contains(startTimeout),
              endSilence.isFinite && (0.6...3).contains(endSilence),
              finalTimeout.isFinite && (1...10).contains(finalTimeout),
              minSpeechDuration.isFinite && (0.3...3).contains(minSpeechDuration),
              minSpeechDuration <= timeout else {
            emit(["status": "invalid_configuration"])
        }
        let locale = Locale(identifier: args[2])
        guard let recognizer = SFSpeechRecognizer(locale: locale) else {
            emit(["status": "recognizer_unavailable"])
        }
        let allowNetwork = args[4] == "network"
        if args[1] == "--check" {
            // Diagnostic only: neither requests permissions nor starts audio capture.
            emit(["status": "ok", "on_device": recognizer.supportsOnDeviceRecognition,
                  "available": recognizer.isAvailable,
                  "microphone_authorization": AVCaptureDevice.authorizationStatus(for: .audio).rawValue,
                  "speech_authorization": SFSpeechRecognizer.authorizationStatus().rawValue])
        }
        let microphoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        if microphoneStatus == .denied || microphoneStatus == .restricted {
            emit(["status": "microphone_denied"])
        }
        func startWhenAuthorized() {
            DispatchQueue.main.async {
                guard recognizer.isAvailable else {
                    emit(["status": "recognizer_unavailable"])
                }
                guard allowNetwork || recognizer.supportsOnDeviceRecognition else {
                    emit(["status": "on_device_unavailable"])
                }
                if args[1] == "--authorize" { emit(["status": "ok"]) }
                session = SpeechSession(recognizer: recognizer, timeout: timeout, allowNetwork: allowNetwork,
                                        startTimeout: startTimeout, endSilence: endSilence,
                                        minSpeechDuration: minSpeechDuration,
                                        finalTimeout: finalTimeout, debug: debug)
                session?.start()
            }
        }
        func authorizeMicrophone() {
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .authorized: startWhenAuthorized()
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .audio) { allowed in
                    if allowed { startWhenAuthorized() }
                    else { emit(["status": "microphone_denied"]) }
                }
            default: emit(["status": "microphone_denied"])
            }
        }
        switch SFSpeechRecognizer.authorizationStatus() {
        case .authorized: authorizeMicrophone()
        case .notDetermined:
            SFSpeechRecognizer.requestAuthorization { status in
                if status == .authorized { authorizeMicrophone() }
                else { emit(["status": "speech_denied"]) }
            }
        default: emit(["status": "speech_denied"])
        }
        RunLoop.main.run()
    }
}
