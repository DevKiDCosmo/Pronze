FROM --platform=linux/amd64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install compilation toolchains, packaging utilities, and target distro dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    bc \
    bison \
    build-essential \
    btrfs-progs \
    ca-certificates \
    cpio \
    curl \
    dosfstools \
    dwarves \
    fdisk \
    flex \
    git \
    libelf-dev \
    libncurses-dev \
    libssl-dev \
    mtools \
    musl-tools \
    perl \
    python3 \
    rsync \
    syslinux \
    syslinux-common \
    systemd-boot \
    wget \
    xz-utils \
  && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain (stable) and add musl target
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
RUN rustup target add x86_64-unknown-linux-musl

# Install Zig toolchain (0.13.0)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then ZIG_ARCH="x86_64"; else ZIG_ARCH="aarch64"; fi && \
    wget -q https://ziglang.org/download/0.13.0/zig-linux-${ZIG_ARCH}-0.13.0.tar.xz && \
    tar -xf zig-linux-${ZIG_ARCH}-0.13.0.tar.xz && \
    mv zig-linux-${ZIG_ARCH}-0.13.0 /opt/zig && \
    ln -s /opt/zig/zig /usr/local/bin/zig && \
    rm zig-linux-${ZIG_ARCH}-0.13.0.tar.xz

WORKDIR /workspace

# Copy workspace directories
COPY . /workspace

# Ensure scripts are executable
RUN chmod +x /workspace/scripts/*.sh

# Run the distro build and packaging script by default
ENTRYPOINT ["/bin/bash", "/workspace/scripts/build-distro.sh"]