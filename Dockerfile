# Dockerfile
# ComfyUI Serverless image with a custom Runpod handler that:
# - decodes base64 input images
# - writes them into ComfyUI input/
# - runs the workflow via ComfyUI HTTP API

FROM runpod/worker-comfyui:5.8.4-base

# Build-time token for gated downloads (DO NOT hardcode real tokens in the Dockerfile)
ARG HF_TOKEN=""

# ---- Custom nodes ----
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/cubiq/ComfyUI_essentials.git

# ---- Models ----
RUN for i in 1 2 3 4 5; do \
      HF_TOKEN=$HF_TOKEN comfy model download \
        --url "https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/resolve/main/v14/Qwen-Rapid-AIO-NSFW-v14.1.safetensors" \
        --relative-path models/checkpoints \
        --filename "Qwen-Rapid-AIO-NSFW-v14.1.safetensors" \
      && break; \
      if [ $i -eq 5 ]; then echo "model-download failed after 5 attempts" >&2; exit 1; fi; \
      echo "model-download attempt $i failed; retrying in $((i*10))s" >&2; \
      sleep $((i*10)); \
    done

# ---- Handler ----
# Put the handler in a known path
WORKDIR /runpod
COPY handler.py /runpod/handler.py

# (Optional) If you add extra deps in handler.py (e.g., requests/boto3), install them here:
# RUN pip install --no-cache-dir requests boto3

# Ensure the handler is what runs in serverless
CMD ["python", "-u", "/runpod/handler.py"]
# clean base image containing only comfyui, comfy-cli and comfyui-manager
FROM runpod/worker-comfyui:5.8.4-base

# build-time tokens for gated downloads — never baked into final image.
# pass via: docker build --build-arg HF_TOKEN=$HF_TOKEN ...
ARG HF_TOKEN="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"

# install custom nodes
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/cubiq/ComfyUI_essentials.git

# download models into comfyui
RUN for i in 1 2 3 4 5; do HF_TOKEN=$HF_TOKEN comfy model download --url 'https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/resolve/main/v14/Qwen-Rapid-AIO-NSFW-v14.1.safetensors' --relative-path models/checkpoints --filename 'Qwen-Rapid-AIO-NSFW-v14.1.safetensors' && break; if [ $i -eq 5 ]; then echo "model-download failed after 5 attempts" >&2; exit 1; fi; echo "model-download attempt $i failed; retrying in $((i*10))s" >&2; sleep $((i*10)); done


