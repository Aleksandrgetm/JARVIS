"""Build the small system-framework bridge on first explicit voice launch."""

import hashlib
from pathlib import Path
import platform
import subprocess
from tempfile import TemporaryDirectory

from voice.errors import SpeechError


def build_native_bridge(build_dir: Path) -> Path:
    if platform.system() != "Darwin":
        raise SpeechError("Voice mode requires macOS.")
    sources = Path(__file__).resolve().parent / "native"
    files = [sources / "Microphone.swift", sources / "SpeechBridge.swift",
             sources / "SpeechTiming.swift", sources / "RecognitionLifecycle.swift", sources / "Info.plist"]
    try:
        digest = hashlib.sha256(b"".join(path.read_bytes() for path in files)).hexdigest()
        build_dir = build_dir.resolve()
        binary = build_dir / "jarvis-speech"
        stamp = build_dir / "source.sha256"
        if binary.is_file() and stamp.is_file() and stamp.read_text() == digest:
            return binary
        build_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=build_dir) as temporary:
            output = Path(temporary) / "jarvis-speech"
            command = ["/usr/bin/xcrun", "swiftc", "-swift-version", "5", "-O",
                       *(str(path) for path in files[:-1]), "-framework", "Speech", "-framework", "AVFoundation",
                       "-module-cache-path", str(build_dir / "module-cache"),
                       "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
                       "-Xlinker", str(files[-1]), "-o", str(output)]
            result = subprocess.run(command, capture_output=True, shell=False, timeout=60)
            if result.returncode != 0:
                raise SpeechError("Could not build Apple Speech bridge. Check Xcode Command Line Tools (xcode-select --install).")
            signing = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-",
                                      "--identifier", "local.jarvis.speech", str(output)],
                                     capture_output=True, shell=False, timeout=15)
            if signing.returncode != 0:
                raise SpeechError("Could not sign the local Apple Speech bridge.")
            output.replace(binary)
            stamp.write_text(digest)
        return binary
    except subprocess.TimeoutExpired:
        raise SpeechError("Apple Speech bridge build timed out. Try launching voice mode again.") from None
    except OSError:
        raise SpeechError("Cannot prepare voice support. Check Command Line Tools and data/voice write access.") from None
