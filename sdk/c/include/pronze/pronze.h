#ifndef PRONZE_H
#define PRONZE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Poison pointer definition
#define PRONZE_POISON_PTR ((void*)0xDEADBEEF)

// Pronze profiling and simulation context
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
} PronzeContext;

// Public SDK API Functions
int pronzeInit(PronzeContext* ctx);
int pronzeLoadProfile(PronzeContext* ctx, const char* profile_path);
int pronzeStartProfiling(PronzeContext* ctx);
void* pronze_malloc(PronzeContext* ctx, size_t size);
void pronze_free(PronzeContext* ctx, void* ptr);
int pronzeShutdown(PronzeContext* ctx);
int pronzeEnableAllocationFailure(PronzeContext* ctx, int failure_rate);

// Telemetry and simulation verification functions
int pronzeSimulateAccess(PronzeContext* ctx, void* ptr);

#ifdef __cplusplus
}
#endif

#endif // PRONZE_H
