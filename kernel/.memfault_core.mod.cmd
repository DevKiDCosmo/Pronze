savedcmd_/workspace/kernel/memfault_core.mod := printf '%s\n'   memfault_core.o | awk '!x[$$0]++ { print("/workspace/kernel/"$$0) }' > /workspace/kernel/memfault_core.mod
