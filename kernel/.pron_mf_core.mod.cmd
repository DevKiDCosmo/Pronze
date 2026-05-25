savedcmd_/workspace/kernel/pron_mf_core.mod := printf '%s\n'   pron_mf_core.o | awk '!x[$$0]++ { print("/workspace/kernel/"$$0) }' > /workspace/kernel/pron_mf_core.mod
