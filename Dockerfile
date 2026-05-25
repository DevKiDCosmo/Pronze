FROM --platform=linux/amd64 ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install core system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    bc \
    bison \
    build-essential \
    ca-certificates \
    cpio \
    curl \
    dwarves \
    flex \
    git \
    libelf-dev \
    libncurses-dev \
    libssl-dev \
    perl \
    python3 \
    rsync \
    wget \
    xz-utils \
  && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain (stable)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

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

# Run compilation and test orchestration by default
ENTRYPOINT ["/bin/bash", "/workspace/scripts/run-all.sh"]