#ifndef MEMFAULT_HPP
#define MEMFAULT_HPP

#include <memfault/memfault.h>
#include <stdexcept>
#include <string>

namespace memfault {

class MemFaultContext {
private:
    MFContext ctx;
    bool initialized;

public:
    MemFaultContext() : initialized(false) {
        if (mfInit(&ctx) != 0) {
            throw std::runtime_error("Failed to initialize MemFault Context");
        }
        initialized = true;
    }

    ~MemFaultContext() {
        if (initialized) {
            mfShutdown(&ctx);
        }
    }

    void loadProfile(const std::string& profile_path) {
        if (mfLoadProfile(&ctx, profile_path.c_str()) != 0) {
            throw std::runtime_error("Failed to load fault profile: " + profile_path);
        }
    }

    void startProfiling() {
        if (mfStartProfiling(&ctx) != 0) {
            throw std::runtime_error("Failed to start profiling");
        }
    }

    void enableAllocationFailure(int failure_rate) {
        if (mfEnableAllocationFailure(&ctx, failure_rate) != 0) {
            throw std::runtime_error("Failed to enable allocation failure");
        }
    }

    void* allocate(size_t size) {
        return mf_malloc(&ctx, size);
    }

    void deallocate(void* ptr) {
        mf_free(&ctx, ptr);
    }

    int simulateAccess(void* ptr) {
        return mfSimulateAccess(&ctx, ptr);
    }

    MFContext* getRawContext() {
        return &ctx;
    }
};

} // namespace memfault

#endif // MEMFAULT_HPP
