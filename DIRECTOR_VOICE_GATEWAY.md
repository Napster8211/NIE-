# Director Voice Engine (ElevenLabs)

Director batch and realtime text-to-speech are powered by ElevenLabs Text-to-Speech API.

### Runtime Configuration

Configure the NIE backend runtime in `.env` with:

```dotenv
# ElevenLabs Voice Configuration
ELEVENLABS_API_KEY=sk_...
ELEVENLABS_VOICE_ID=5HbhilP8ETKTLXJUcZw7
ELEVENLABS_MODEL_ID=eleven_multilingual_v2
ELEVENLABS_TIMEOUT_SECONDS=30
MAX_DIRECTOR_SPEECH_CHARS=5000
```

`ELEVENLABS_API_KEY` is an internal service credential. Do not commit its real value or expose it to browser clients.
Audio output format is `audio/mpeg` (MP3, 44.1kHz), supported natively across all modern web browsers and mobile clients.
