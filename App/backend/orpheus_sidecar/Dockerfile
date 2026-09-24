# Orpheus-3B TTS sidecar for the receptionist's Luganda voice.
# vLLM brings its own torch/transformers; that is why this is a separate
# image rather than a dependency of the api image. See app.py's docstring.
FROM vllm/vllm-openai:v0.17.1

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

WORKDIR /srv
COPY codes.py app.py /srv/

ENV HF_HOME=/app/hf_cache \
    VLLM_WORKER_MULTIPROC_METHOD=spawn
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=5 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
