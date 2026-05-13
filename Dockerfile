FROM runpod/worker-comfyui:5.8.4-base

ARG HF_TOKEN=""

# ---------- Download Model ----------
RUN for i in 1 2 3 4 5; do \
      HF_TOKEN=$HF_TOKEN comfy model download \
      --url "https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/resolve/main/v14/Qwen-Rapid-AIO-NSFW-v14.1.safetensors" \
      --relative-path models/checkpoints \
      --filename "Qwen-Rapid-AIO-NSFW-v14.1.safetensors" \
      && break; \
      if [ $i -eq 5 ]; then \
        echo "Model download failed" >&2; \
        exit 1; \
      fi; \
      echo "Retrying model download..." >&2; \
      sleep $((i*10)); \
    done

# ---------- Handler ----------
WORKDIR /runpod

COPY handler.py /runpod/handler.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "/runpod/handler.py"]