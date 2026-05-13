# clean base image containing only comfyui, comfy-cli and comfyui-manager
FROM runpod/worker-comfyui:5.8.4-base

# build args
ARG HF_TOKEN

# install custom nodes
RUN cd /comfyui/custom_nodes && \
    git clone https://github.com/cubiq/ComfyUI_essentials.git

# download model
RUN for i in 1 2 3 4 5; do \
    HF_TOKEN=$HF_TOKEN comfy model download \
    --url 'https://huggingface.co/Phr00t/Qwen-Image-Edit-Rapid-AIO/resolve/main/v14/Qwen-Rapid-AIO-NSFW-v14.1.safetensors' \
    --relative-path models/checkpoints \
    --filename 'Qwen-Rapid-AIO-NSFW-v14.1.safetensors' \
    && break; \
    if [ $i -eq 5 ]; then \
        echo "model-download failed after 5 attempts" >&2; \
        exit 1; \
    fi; \
    echo "model-download attempt $i failed; retrying in $((i*10))s" >&2; \
    sleep $((i*10)); \
done
