#ifndef PRONMF_HPP
#define PRONMF_HPP

#include <pmf/pmf.h>
#include <stdexcept>
#include <string>

namespace pronmf {

class PronMFContext {
private:
    PMFContext ctx;
    bool initialized;

public:
    PronMFContext() : initialized(false) {
        if (pmfInit(&ctx) != 0) {
            throw std::runtime_error("Failed to initialize Pron MF Context");
        }
        initialized = true;
    }

    ~PronMFContext() {
        if (initialized) {
            pmfShutdown(&ctx);
        }
    }

    void loadProfile(const std::string& profile_path) {
        if (pmfLoadProfile(&ctx, profile_path.c_str()) != 0) {
            throw std::runtime_error("Failed to load fault profile: " + profile_path);
        }
    }

    void startProfiling() {
        if (pmfStartProfiling(&ctx) != 0) {
            throw std::runtime_error("Failed to start profiling");
        }
    }

    void enableAllocationFailure(int failure_rate) {
        if (pmfEnableAllocationFailure(&ctx, failure_rate) != 0) {
            throw std::runtime_error("Failed to enable allocation failure");
        }
    }

    void* allocate(size_t size) {
        return pmf_malloc(&ctx, size);
    }

    void deallocate(void* ptr) {
        pmf_free(&ctx, ptr);
    }

    int simulateAccess(void* ptr) {
        return pmfSimulateAccess(&ctx, ptr);
    }

    PMFContext* getRawContext() {
        return &ctx;
    }
};

} // namespace pronmf

#endif // PRONMF_HPP
