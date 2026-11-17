# Piper TTS HTTP server for German/Dutch voices (EU languages Kokoro lacks).
# Speaks to pipecat's PiperHttpTTSService (POST / with {"text","voice"} -> WAV).
# Additive: a new optional Voice provider; does not touch Kokoro/Speaches.
FROM python:3.11-slim

# Pinned: 1.5+ moved synthesis to POST /synthesize; pipecat's PiperHttpTTSService posts to /.
RUN pip install --no-cache-dir "piper-tts[http]==1.4.2"

WORKDIR /voices

# Pre-download voices at build time so the container is self-contained and works
# offline. Retry to ride out transient SSL/network blips from the voice host.
# German/Dutch voices cover the EU languages Kokoro lacks; the server loads
# whichever downloaded voice the request names ({"voice": ...}).
RUN for i in 1 2 3 4 5; do \
      python -m piper.download_voices \
        de_DE-thorsten-high de_DE-thorsten_emotional-medium de_DE-kerstin-low \
        nl_NL-mls-medium nl_BE-nathalie-medium nl_BE-rdh-medium \
        && break || sleep 5; \
    done

EXPOSE 5000

# -m is only the default voice; others in /voices load on first request.
CMD ["python", "-m", "piper.http_server", "--host", "0.0.0.0", "--port", "5000", "-m", "de_DE-thorsten-high"]
