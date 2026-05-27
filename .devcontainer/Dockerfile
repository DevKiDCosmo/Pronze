ARG VARIANT=24.04
FROM mcr.microsoft.com/devcontainers/base:ubuntu-${VARIANT}

ENV DEBIAN_FRONTEND=noninteractive

# Toolchain for Linux kernel module development and debugging on x86_64.
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
    linux-headers-generic \
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