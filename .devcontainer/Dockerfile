FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

ENV DEBIAN_FRONTEND=noninteractive

# Toolchain for Linux kernel module development and debugging.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bc \
    bison \
    build-essential \
    ccache \
    coccinelle \
    clang \
    clangd \
    dwarves \
    flex \
    gcc \
    g++ \
    git \
    kmod \
    libelf-dev \
    libncurses-dev \
    libssl-dev \
    lld \
    llvm \
    make \
    pkg-config \
    python3 \
    rsync \
    sudo \
    vim \
    wget \
    xz-utils \
    zstd \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspaces/PronKern