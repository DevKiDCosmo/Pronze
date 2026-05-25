#!/usr/bin/env bash

# Shared logging helpers for all PronKern shell scripts.
# Colors are enabled automatically on TTYs and can be forced with FORCE_COLOR=1
# or disabled with NO_COLOR=1.

if [[ -n "${LOG_LIB_SOURCED:-}" ]]; then
    return 0 2>/dev/null || exit 0
fi
readonly LOG_LIB_SOURCED=1

if [[ -t 1 && -z "${NO_COLOR:-}" ]] || [[ "${FORCE_COLOR:-0}" == "1" ]]; then
    LOG_RESET=$'\033[0m'
    LOG_BOLD=$'\033[1m'
    LOG_DIM=$'\033[2m'
    LOG_RED=$'\033[31m'
    LOG_GREEN=$'\033[32m'
    LOG_YELLOW=$'\033[33m'
    LOG_BLUE=$'\033[34m'
    LOG_MAGENTA=$'\033[35m'
    LOG_CYAN=$'\033[36m'
else
    LOG_RESET=""
    LOG_BOLD=""
    LOG_DIM=""
    LOG_RED=""
    LOG_GREEN=""
    LOG_YELLOW=""
    LOG_BLUE=""
    LOG_MAGENTA=""
    LOG_CYAN=""
fi

log_plain() {
    printf '%s\n' "$*"
}

log_step() {
    printf '%b[•]%b %s\n' "$LOG_CYAN" "$LOG_RESET" "$*"
}

log_info() {
    printf '%b[i]%b %s\n' "$LOG_BLUE" "$LOG_RESET" "$*"
}

log_success() {
    printf '%b[✔]%b %s\n' "$LOG_GREEN" "$LOG_RESET" "$*"
}

log_warn() {
    printf '%b[!]%b %s\n' "$LOG_YELLOW" "$LOG_RESET" "$*"
}

log_error() {
    printf '%b[x]%b %s\n' "$LOG_RED" "$LOG_RESET" "$*" >&2
}

log_section() {
    local title="${1:-}"
    local width="${2:-58}"
    local line
    line="$(printf '%*s' "$width" '' | tr ' ' '=')"
    if [[ -z "$title" ]]; then
        printf '%b%s%b\n' "$LOG_MAGENTA" "$line" "$LOG_RESET"
        return 0
    fi
    printf '%b%s%b\n' "$LOG_MAGENTA" "$line" "$LOG_RESET"
    printf '%b%s%b\n' "$LOG_BOLD" "$title" "$LOG_RESET"
    printf '%b%s%b\n' "$LOG_MAGENTA" "$line" "$LOG_RESET"
}
