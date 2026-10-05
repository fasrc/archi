# syntax=docker/dockerfile:1
# The torch version in this tag MUST equal the torch pin in
# requirements/gpu-requirementsHEADER.txt, and its CUDA version must match the
# nvidia-*-cu12 wheels that requirement set resolves to. Otherwise pip installs the
# pinned torch wheel over the one in this image and leaves the older CUDA libraries
# underneath: the build succeeds and the failure lands at GPU import or kernel
# launch, because vllm and xformers carry native extensions built for one exact
# torch/CUDA pair. tests/unit/test_base_image_dependency_compatibility.py enforces
# the torch half of that.
#
# Moved from 2.6.0-cuda12.4 on 2026-09-15 with the torch 2.7.0 bump for #472. CUDA
# 12.6 is not a free choice: the set resolves nvidia-cublas-cu12 12.6.4.1,
# nvidia-cuda-runtime-cu12 12.6.77 and nvidia-cudnn-cu12 9.5.1.17.
FROM docker.io/pytorch/pytorch:2.7.0-cuda12.6-cudnn9-devel
RUN mkdir -p /root/archi
WORKDIR /root/archi

COPY requirements.txt requirements.txt
COPY LICENSE LICENSE

RUN pip install --upgrade pip && pip install -r requirements.txt
