import AVFoundation

// The capture layer only streams memory buffers; it never writes audio to disk.
final class MicrophoneCapture {
    private let engine = AVAudioEngine()
    private var tapped = false

    func start(consume: @escaping (AVAudioPCMBuffer, Float) -> Void) throws {
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 && format.channelCount > 0 else {
            throw NSError(domain: "JARVIS.Microphone", code: 1)
        }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            // RMS energy in dBFS; inspect all channels, without retaining audio.
            var peakRMS: Float = 0
            if let channels = buffer.floatChannelData, buffer.frameLength > 0 {
                for channel in 0..<Int(buffer.format.channelCount) {
                    var sum: Float = 0
                    for frame in 0..<Int(buffer.frameLength) {
                        let sample = channels[channel][frame * buffer.stride]
                        sum += sample * sample
                    }
                    peakRMS = max(peakRMS, sqrt(sum / Float(buffer.frameLength)))
                }
            }
            consume(buffer, 20 * log10(max(peakRMS, 0.0000001)))
        }
        tapped = true
        engine.prepare()
        do {
            try engine.start()
        } catch {
            stop()
            throw error
        }
    }

    func stop() {
        engine.stop()
        if tapped {
            engine.inputNode.removeTap(onBus: 0)
            tapped = false
        }
    }

    deinit { stop() }
}
