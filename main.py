"""JARVIS entry point."""

import argparse

from core.assistant import Assistant
from core.config import Config
from core.logger import setup_logger


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="JARVIS local macOS assistant")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--text", action="store_true", help="Run the text CLI (default)")
    modes.add_argument("--voice", action="store_true", help="Run an active voice session")
    parser.add_argument("--debug", action="store_true", help="Print live voice diagnostics; requires --voice")
    parser.add_argument("--voice-allow-network", action="store_true",
                        help="Allow Apple Speech servers; requires --voice")
    args = parser.parse_args(argv)
    if args.voice_allow_network and not args.voice:
        parser.error("--voice-allow-network requires --voice")
    if args.debug and not args.voice:
        parser.error("--debug requires --voice")
    config = Config(voice_allow_network=args.voice_allow_network, voice_debug=args.debug)
    logger = setup_logger(config.log_dir)
    if args.voice:
        # Import native voice support only when explicitly requested.
        from core.bootstrap import create_router
        from voice.listener import NativeMicrophoneListener
        from voice.speech_to_text import AppleSpeechToText
        from voice.text_to_speech import MacOSTextToSpeech
        from voice.voice_assistant import VoiceAssistant

        speech = AppleSpeechToText(NativeMicrophoneListener(config))
        VoiceAssistant(config, logger, create_router(config, logger), speech,
                       MacOSTextToSpeech(config.tts_voice)).run()
    else:
        Assistant(config=config, logger=logger).run()


if __name__ == "__main__":
    main()
