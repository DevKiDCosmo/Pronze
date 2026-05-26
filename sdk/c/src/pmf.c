#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <pmf/pmf.h>

#define MAJOR_NUM 240
#define DEVICE_PATH "/dev/pron_mf"

#define PMF_IOCTL_ENABLE_FAULT  0x1001
#define PMF_IOCTL_SET_PROFILE   0x1002
#define PMF_IOCTL_START_TRACE   0x1003
#define PMF_IOCTL_RECORD_MALLOC 0x1004

// Initialize context
int pmfInit(PMFContext* ctx) {
    if (!ctx) return -1;
    
    // Seed the randomizer for user-space simulation faults
    srand((unsigned int)time(NULL));
    
    ctx->failure_rate = 0;
    
    // Try to open kernel device driver
    int fd = open(DEVICE_PATH, O_RDWR);
    if (fd >= 0) {
        ctx->fd = fd;
        ctx->simulation_mode = 0;
        ctx->sim_outer_space = NULL;
        ctx->sim_outer_size = 0;
        ctx->sim_inner_space = NULL;
        ctx->sim_inner_size = 0;
        ctx->sim_offset = 0;
        printf("[+] Pron MF SDK: Kernel Mode Enabled. Connected to %s\n", DEVICE_PATH);
    } else {
        // Fallback to User-Space Simulation Container Mode
        ctx->fd = -1;
        ctx->simulation_mode = 1;
        
        // Define space sizes
        ctx->sim_outer_size = 8 * 1024 * 1024; // 8MB Outer Space (Simulated RAM)
        ctx->sim_inner_size = 4 * 1024 * 1024; // 4MB Inner Space (Application Pool)
        
        // Map Outer Space
        ctx->sim_outer_space = mmap(NULL, ctx->sim_outer_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (ctx->sim_outer_space == MAP_FAILED) {
            perror("[-] Pron MF SDK: mmap failed during simulation initialization");
            return -1;
        }
        
        // Inner Space starts at 2MB offset inside Outer Space (creating guard zones before and after)
        ctx->sim_inner_space = (void*)((uintptr_t)ctx->sim_outer_space + 2 * 1024 * 1024);
        ctx->sim_offset = 0;
        
        // Zero-out the mapped region to simulate clean memory
        memset(ctx->sim_outer_space, 0, ctx->sim_outer_size);
        
        printf("[+] Pron MF SDK: User-space Simulation Container Mode Enabled.\n");
        printf("    - Outer Space (Simulated System): %p (%zu bytes)\n", ctx->sim_outer_space, ctx->sim_outer_size);
        printf("    - Inner Space (Application Pool): %p (%zu bytes)\n", ctx->sim_inner_space, ctx->sim_inner_size);
    }
    
    return 0;
}

// Load fault profile from JSON file (simple robust parser)
int pmfLoadProfile(PMFContext* ctx, const char* profile_path) {
    if (!ctx) return -1;
    
    FILE* f = fopen(profile_path, "r");
    if (!f) {
        printf("[-] Pron MF SDK Warning: Failed to open profile %s\n", profile_path);
        return -1;
    }
    
    char buffer[1024];
    size_t read_bytes = fread(buffer, 1, sizeof(buffer) - 1, f);
    buffer[read_bytes] = '\0';
    fclose(f);
    
    // Look for allocation_failure_rate
    char* rate_ptr = strstr(buffer, "\"allocation_failure_rate\"");
    if (rate_ptr) {
        char* val_ptr = strchr(rate_ptr, ':');
        if (val_ptr) {
            int rate = 0;
            if (sscanf(val_ptr + 1, "%d", &rate) == 1) {
                pmfEnableAllocationFailure(ctx, rate);
            }
        }
    }
    return 0;
}

// Enable trace
int pmfStartProfiling(PMFContext* ctx) {
    if (!ctx) return -1;
    
    if (ctx->simulation_mode == 0) {
        if (ioctl(ctx->fd, PMF_IOCTL_START_TRACE) < 0) {
            perror("[-] Pron MF SDK: ioctl PMF_IOCTL_START_TRACE failed");
            return -1;
        }
    } else {
        printf("[+] Pron MF SDK: User-space simulation trace started.\n");
    }
    return 0;
}

// Enable allocation failure rate
int pmfEnableAllocationFailure(PMFContext* ctx, int failure_rate) {
    if (!ctx) return -1;
    if (failure_rate < 0 || failure_rate > 100) return -1;
    
    ctx->failure_rate = failure_rate;
    printf("[+] Pron MF SDK: Set allocation failure rate to %d%%\n", failure_rate);
    
    if (ctx->simulation_mode == 0) {
        if (ioctl(ctx->fd, PMF_IOCTL_ENABLE_FAULT, failure_rate) < 0) {
            perror("[-] Pron MF SDK: ioctl PMF_IOCTL_ENABLE_FAULT failed");
            return -1;
        }
    }
    return 0;
}

// Allocate memory (with optional fault simulation)
void* pmf_malloc(PMFContext* ctx, size_t size) {
    if (!ctx) return NULL;
    
    void* ptr = NULL;
    int triggered_failure = 0;
    
    // Inject deterministic allocation failure based on rate config
    if (ctx->failure_rate > 0) {
        int r = rand() % 100;
        if (r < ctx->failure_rate) {
            triggered_failure = 1;
        }
    }
    
    if (triggered_failure) {
        ptr = NULL;
    } else {
        if (ctx->simulation_mode == 0) {
            ptr = malloc(size);
        } else {
            // Bump allocation within simulation inner space (Application Pool)
            // Align block to 8 bytes
            size_t aligned_size = (size + 7) & ~7;
            
            if (ctx->sim_offset + aligned_size > ctx->sim_inner_size) {
                printf("[-] Pron MF SDK Simulation: Out of memory in Application Pool!\n");
                ptr = NULL;
            } else {
                ptr = (void*)((uintptr_t)ctx->sim_inner_space + ctx->sim_offset);
                ctx->sim_offset += aligned_size;
            }
        }
    }
    
    // In kernel mode, notify the driver of success/failure
    if (ctx->simulation_mode == 0 && ctx->fd >= 0) {
        ioctl(ctx->fd, PMF_IOCTL_RECORD_MALLOC, ptr == NULL ? 1 : 0);
    }
    
    return ptr;
}

// Free memory
void pmf_free(PMFContext* ctx, void* ptr) {
    if (!ctx || !ptr) return;
    
    if (ctx->simulation_mode == 0) {
        free(ptr);
    } else {
        // In simple bump allocator, free is a logical event.
        // We ensure the pointer is within the Inner Space boundary
        uintptr_t addr = (uintptr_t)ptr;
        uintptr_t inner_start = (uintptr_t)ctx->sim_inner_space;
        uintptr_t inner_end = inner_start + ctx->sim_inner_size;
        
        if (addr >= inner_start && addr < inner_end) {
            // Free succeeded logically
        } else {
            printf("[-] Pron MF SDK Error: Invalid free on pointer %p outside Application Pool!\n", ptr);
        }
    }
}

// Verify memory access bounds and log telemetry
int pmfSimulateAccess(PMFContext* ctx, void* ptr) {
    if (!ctx || !ptr) return -2;
    
    if (ctx->simulation_mode == 0) {
        if ((uintptr_t)ptr == 0xBAADF00D) {
            return -2;
        }
        return 0;
    }
    
    uintptr_t addr = (uintptr_t)ptr;
    uintptr_t inner_start = (uintptr_t)ctx->sim_inner_space;
    uintptr_t inner_end = inner_start + ctx->sim_inner_size;
    uintptr_t outer_start = (uintptr_t)ctx->sim_outer_space;
    uintptr_t outer_end = outer_start + ctx->sim_outer_size;
    
    if (addr >= inner_start && addr < inner_end) {
        // Valid application access
        return 0;
    } else if (addr >= outer_start && addr < outer_end) {
        // Out-of-bounds simulation breach!
        printf("[!] Pron Telemetry: Containment breach! Out-of-bounds access detected at address %p\n", ptr);
        printf("    - Outer Space range: [%p - %p]\n", (void*)outer_start, (void*)outer_end);
        printf("    - Inner Space range: [%p - %p]\n", (void*)inner_start, (void*)inner_end);
        return -1;
    } else {
        // Complete segmentation fault
        printf("[CRITICAL] Pron Telemetry: Fault access outside sandbox memory space at address %p\n", ptr);
        return -2;
    }
}

// Clean up and shutdown
int pmfShutdown(PMFContext* ctx) {
    if (!ctx) return -1;
    
    if (ctx->simulation_mode == 0) {
        if (ctx->fd >= 0) {
            close(ctx->fd);
            ctx->fd = -1;
        }
    } else {
        if (ctx->sim_outer_space && ctx->sim_outer_space != MAP_FAILED) {
            munmap(ctx->sim_outer_space, ctx->sim_outer_size);
            ctx->sim_outer_space = NULL;
        }
    }
    printf("[+] Pron MF SDK: Shutdown complete.\n");
    return 0;
}
