# Piper TTS HTTP server for self-hosted Urdu (and other) voices.
# Speaks to pipecat's PiperHttpTTSService (POST / with {"text","voice"} -> WAV).
# Additive: a new optional Voice provider; does not touch Kokoro/Speaches.
FROM python:3.11-slim

RUN pip install --no-cache-dir "piper-tts[http]"

WORKDIR /voices

# Pre-download voices at build time so the container is self-contained and works
# offline. Retry to ride out transient SSL/network blips from the voice host.
RUN for i in 1 2 3 4 5; do \
      python -m piper.download_voices ur_PK-fasih-medium && break || sleep 5; \
    done

EXPOSE 5000

# Single-voice server; the voice is fixed at startup via -m. pipecat sends the
# text in the POST body. To add more voices later, run additional instances or
# extend the entrypoint.
CMD ["python", "-m", "piper.http_server", "--host", "0.0.0.0", "--port", "5000", "-m", "ur_PK-fasih-medium"]
