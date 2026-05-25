#ifndef PMF_H
#define PMF_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Poison pointer definition
#define PMF_POISON_PTR ((void*)0xDEADBEEF)

// Pron MF profiling and simulation context
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
} PMFContext;

// Public SDK API Functions
int pmfInit(PMFContext* ctx);
int pmfLoadProfile(PMFContext* ctx, const char* profile_path);
int pmfStartProfiling(PMFContext* ctx);
void* pmf_malloc(PMFContext* ctx, size_t size);
void pmf_free(PMFContext* ctx, void* ptr);
int pmfShutdown(PMFContext* ctx);
int pmfEnableAllocationFailure(PMFContext* ctx, int failure_rate);

// Telemetry and simulation verification functions
int pmfSimulateAccess(PMFContext* ctx, void* ptr);

#ifdef __cplusplus
}
#endif

#endif // PMF_H
