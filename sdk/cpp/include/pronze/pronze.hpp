#ifndef PRONZE_HPP
#define PRONZE_HPP

#include <pronze/pronze.h>
#include <stdexcept>
#include <string>

namespace pronze {

class PronzeContext {
private:
    ::PronzeContext ctx;
    bool initialized;

public:
    PronzeContext() : initialized(false) {
        if (pronzeInit(&ctx) != 0) {
            throw std::runtime_error("Failed to initialize Pronze Context");
        }
        initialized = true;
    }

    ~PronzeContext() {
        if (initialized) {
            pronzeShutdown(&ctx);
        }
    }

    void loadProfile(const std::string& profile_path) {
        if (pronzeLoadProfile(&ctx, profile_path.c_str()) != 0) {
            throw std::runtime_error("Failed to load fault profile: " + profile_path);
        }
    }

    void startProfiling() {
        if (pronzeStartProfiling(&ctx) != 0) {
            throw std::runtime_error("Failed to start profiling");
        }
    }

    void enableAllocationFailure(int failure_rate) {
        if (pronzeEnableAllocationFailure(&ctx, failure_rate) != 0) {
            throw std::runtime_error("Failed to enable allocation failure");
        }
    }

    void* allocate(size_t size) {
        return pronze_malloc(&ctx, size);
    }

    void deallocate(void* ptr) {
        pronze_free(&ctx, ptr);
    }

    int simulateAccess(void* ptr) {
        return pronzeSimulateAccess(&ctx, ptr);
    }

    ::PronzeContext* getRawContext() {
        return &ctx;
    }
};

} // namespace pronze

#endif // PRONZE_HPP
