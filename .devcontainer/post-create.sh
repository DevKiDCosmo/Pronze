#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../scripts/utils/log_lib.sh
source "$SCRIPT_DIR/../scripts/utils/log_lib.sh"

create_cpp_properties() {
    local workspace="/workspaces/PronzeOS"
    local vscode_dir="$workspace/.vscode"
    local cpp_props="$vscode_dir/c_cpp_properties.json"
    local clangd_cfg="$workspace/.clangd"
    local kbuild="/lib/modules/$(uname -r)/build"

    local -a header_roots=()
    local -a include_paths=("\${workspaceFolder}/**")
    local -A seen=()

    add_path() {
        local path="$1"
        if [ -d "$path" ] && [ -z "${seen[$path]+x}" ]; then
            include_paths+=("$path")
            seen["$path"]=1
        fi
    }

    if [ -d "$kbuild" ]; then
        header_roots+=("$kbuild")
        log_success "Using running-kernel build tree for IntelliSense: $kbuild"
    fi

    while IFS= read -r dir; do
        header_roots+=("$dir")
    done < <(find /usr/src -maxdepth 1 -type d -name 'linux-headers-*' | sort -V)

    if [ "${#header_roots[@]}" -eq 0 ]; then
        log_warn "No kernel headers found for IntelliSense configuration."
        return 0
    fi

    for root in "${header_roots[@]}"; do
        add_path "$root/include"
        add_path "$root/include/uapi"
        add_path "$root/include/generated"
        add_path "$root/include/generated/uapi"

        if [ -d "$root/arch" ]; then
            while IFS= read -r archdir; do
                add_path "$archdir/include"
                add_path "$archdir/include/uapi"
                add_path "$archdir/include/generated"
                add_path "$archdir/include/generated/uapi"
            done < <(find "$root/arch" -mindepth 1 -maxdepth 1 -type d | sort)
        fi
    done

    mkdir -p "$vscode_dir"

    {
        echo '{'
        echo '  "configurations": ['
        echo '    {'
        echo '      "name": "Linux Kernel",'
        echo '      "includePath": ['
        for i in "${!include_paths[@]}"; do
            if [ "$i" -lt "$((${#include_paths[@]} - 1))" ]; then
                echo "        \"${include_paths[$i]}\"," 
            else
                echo "        \"${include_paths[$i]}\""
            fi
        done
        echo '      ],'
        echo '      "defines": ['
        echo '        "__KERNEL__",'
        echo '        "MODULE"'
        echo '      ],'
        echo '      "compilerPath": "/usr/bin/clang",'
        echo '      "intelliSenseMode": "linux-clang-x64",'
        echo '      "cStandard": "gnu17",'
        echo '      "cppStandard": "gnu++20"'
        echo '    }'
        echo '  ],'
        echo '  "version": 4'
        echo '}'
    } > "$cpp_props"

    {
        echo 'CompileFlags:'
        echo '  Add:'
        echo '    - -D__KERNEL__'
        echo '    - -DMODULE'
        echo '    - -Wno-unknown-attributes'
        echo '    - -Wno-gnu-offsetof-extensions'
        echo '    - -Wno-language-extension-token'
        for path in "${include_paths[@]}"; do
            if [ "$path" = "\${workspaceFolder}/**" ]; then
                echo "    - -I$workspace"
            else
                echo "    - -I$path"
            fi
        done
    } > "$clangd_cfg"

    log_success "Generated VS Code C/C++ kernel IntelliSense config: $cpp_props"
    log_success "Generated clangd fallback config: $clangd_cfg"
}

log_section "PronzeOS Dev Container Ready" 58

mkdir -p build logs output

KDIR="/lib/modules/$(uname -r)/build"
if [ -d "$KDIR" ]; then
    log_success "Kernel build tree found: $KDIR"
    log_info "Build the module with: make -C \"$KDIR\" M=\"$PWD/kernel\" modules"
else
    log_warn "Kernel build tree not found at: $KDIR"
    log_warn "On Linux hosts, bind-mount matching headers or set KDIR manually before building."
fi

log_info "Suggested in-container command: make -C /lib/modules/\$(uname -r)/build M=\"$PWD/kernel\" modules"

create_cpp_properties
