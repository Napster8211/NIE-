# Director Piper voice gateway

Director batch and realtime text-to-speech use the private NapsterTec Voice
Gateway. Configure the NIE backend runtime with:

```dotenv
VOICE_GATEWAY_URL=https://napstertecvo.onrender.com
VOICE_GATEWAY_API_KEY=<same internal key configured on the voice gateway>
VOICE_GATEWAY_TIMEOUT_SECONDS=45
DIRECTOR_PIPER_SAMPLE_RATE=16000
```

`VOICE_GATEWAY_API_KEY` is an internal service credential. Do not commit its
real value or expose it to browser clients. ElevenLabs configuration remains in
use only for the existing speech-to-text service.
