# Synthetic local speech fixture

`speech-question.wav` says “What is my next class?” It was synthesized locally
with Windows System.Speech for an offline STT/browser test. It contains no campus
recording, personal voice, credentials or user data. It is not evidence of human
speech, noisy microphone or multilingual recognition quality.

Playwright feeds this file to Chrome's fake microphone; it never enables the
operator's physical microphone. Backend tests pass these bytes directly to the
real local Whisper-tiny model when the optional approved assets are installed.
