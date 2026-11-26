# syntax=docker/dockerfile:1
# OPTIONAL ComfyUI service (compose profile "comfyui"). Not built or started
# by default. Large image (CUDA torch wheels, several GB). Models are not
# included: put them in the comfyui-models volume yourself.
FROM python:3.11-slim-bookworm

ARG COMFYUI_REF=master
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128

# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 comfy

RUN git clone --depth 1 --branch "${COMFYUI_REF}" https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI \
 && chown -R comfy:comfy /opt/ComfyUI

WORKDIR /opt/ComfyUI
# hadolint ignore=DL3013
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url "${TORCH_INDEX}" \
 && pip install --no-cache-dir -r requirements.txt

USER 1000
EXPOSE 8188
# --lowvram keeps Stable Diffusion within 6 GB VRAM
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188", "--lowvram"]
