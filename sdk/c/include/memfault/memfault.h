#ifndef MEMFAULT_H
#define MEMFAULT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Poison pointer definition
#define MF_POISON_PTR ((void*)0xDEADBEEF)

// MemFault profiling and simulation context
typedef struct {
    int fd;                       // Device file descriptor (for kernel mode)
    int failure_rate;             // Failure rate percentage (0 to 100)
    int simulation_mode;          // 0 = Kernel mode, 1 = User-space simulation mode
    
    // User-space simulation container parameters
    void* sim_outer_space;        // The mapped "Outer Space" (simulated system memory)
    size_t sim_outer_size;        // Total size of Outer Space
    void* sim_inner_space;        // The "Inner Space" (Application Pool)
    size_t sim_inner_size;        // Size of Application Pool
    size_t sim_offset;            // Bump allocator offset in Inner Space
} MFContext;

// Public SDK API Functions
int mfInit(MFContext* ctx);
int mfLoadProfile(MFContext* ctx, const char* profile_path);
int mfStartProfiling(MFContext* ctx);
void* mf_malloc(MFContext* ctx, size_t size);
void mf_free(MFContext* ctx, void* ptr);
int mfShutdown(MFContext* ctx);
int mfEnableAllocationFailure(MFContext* ctx, int failure_rate);

// Telemetry and simulation verification functions
int mfSimulateAccess(MFContext* ctx, void* ptr);

#ifdef __cplusplus
}
#endif

#endif // MEMFAULT_H
