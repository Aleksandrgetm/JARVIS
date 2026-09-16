"""JARVIS entry point."""

import argparse
import sys
from contextlib import nullcontext
from service.instance import InstanceLock, AlreadyRunning
from service.paths import ServicePaths
from core.settings import ConfigurationError

from core.assistant import Assistant
from core.config import Config
from core.logger import setup_logger
from brain.brain import create_brain


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="JARVIS local macOS assistant")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--text", action="store_true", help="Run the text CLI (default)")
    modes.add_argument("--voice", action="store_true", help="Run an active voice session")
    modes.add_argument("--daemon", action="store_true", help="launchd background Core/TTS; no listening")
    parser.add_argument("--debug", action="store_true", help="Print live voice diagnostics; requires --voice")
    parser.add_argument("--voice-allow-network", action="store_true",
                        help="Allow Apple Speech servers; requires --voice")
    args = parser.parse_args(argv)
    if args.voice_allow_network and not args.voice:
        parser.error("--voice-allow-network requires --voice")
    if args.debug and not args.voice:
        parser.error("--debug requires --voice")
    if args.daemon:
        from service.daemon import run_daemon
        return run_daemon()
    try:
        config = Config.from_env(voice_allow_network=args.voice_allow_network, voice_debug=args.debug)
    except ConfigurationError as error:
        print(str(error))
        return 1
    logger = setup_logger(config.log_dir)
    brain = create_brain(config, logger)
    try:
        with InstanceLock(ServicePaths().lock) if args.voice else nullcontext():
            if args.voice:
                # Import native voice support only when explicitly requested.
                from core.bootstrap import create_router
                from voice.listener import NativeMicrophoneListener
                from voice.speech_to_text import AppleSpeechToText
                from voice.text_to_speech import MacOSTextToSpeech
                from voice.voice_assistant import VoiceAssistant

                speech = AppleSpeechToText(NativeMicrophoneListener(config))
                VoiceAssistant(config, logger, create_router(config, logger), speech,
                               MacOSTextToSpeech(config.tts_voice), brain=brain).run()
            else:
                Assistant(config=config, logger=logger, brain=brain).run()
    except AlreadyRunning:
        print("JARVIS уже запущен.")
        return 0
    finally:
        brain.client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # SystemExit from argparse deliberately retains its exit code and stderr.
        if "--daemon" not in sys.argv[1:]:
            raise
        from service.diagnostics import report_startup_failure
        report_startup_failure(error)
        raise SystemExit(1)
