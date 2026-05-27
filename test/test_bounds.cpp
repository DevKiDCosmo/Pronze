#include <iostream>
#include <pronze/pronze.hpp>

int main() {
    std::cout << "[+] Starting C++ memory containment boundary verification test..." << std::endl;
    
    try {
        pronze::PronzeContext mfc;
        
        // 1. Valid allocation test
        void* valid_ptr = mfc.allocate(1024);
        if (!valid_ptr) {
            std::cerr << "[-] Error: Failed to allocate memory inside container pool" << std::endl;
            return 1;
        }
        
        int status_valid = mfc.simulateAccess(valid_ptr);
        std::cout << "[+] Valid allocation access test: status=" << status_valid 
                  << " (Expected: 0 / SUCCESS)" << std::endl;
        
        if (status_valid != 0) {
            std::cerr << "[-] Verification failed for valid pointer access" << std::endl;
            return 1;
        }
        
        // 2. Out-of-bounds containment breach simulation
        // Calculate an address that is inside the "Outer Space" but outside the "Inner Space" (Application Pool)
        ::PronzeContext* raw_ctx = mfc.getRawContext();
        if (raw_ctx->simulation_mode == 1) {
            // Inner Space starts at 2MB offset, so 1MB offset is inside Outer Space but outside Inner Space
            uintptr_t breach_addr = reinterpret_cast<uintptr_t>(raw_ctx->sim_outer_space) + 1024 * 1024;
            void* breach_ptr = reinterpret_cast<void*>(breach_addr);
            
            int status_breach = mfc.simulateAccess(breach_ptr);
            std::cout << "[+] Simulated container breach access test: status=" << status_breach 
                      << " (Expected: -1 / BREACH)" << std::endl;
            
            if (status_breach != -1) {
                std::cerr << "[-] Verification failed: Container breach was not detected!" << std::endl;
                return 1;
            }
        } else {
            std::cout << "[i] Running in Kernel Mode: Skipping simulation-specific bounds tests." << std::endl;
        }
        
        // 3. Out-of-sandbox critical fault simulation
        void* critical_ptr = reinterpret_cast<void*>(0xBAADF00D);
        int status_critical = mfc.simulateAccess(critical_ptr);
        std::cout << "[+] Simulated out-of-sandbox critical access test: status=" << status_critical 
                  << " (Expected: -2 / CRITICAL)" << std::endl;
        
        if (status_critical != -2) {
            std::cerr << "[-] Verification failed: Critical out-of-sandbox access was not caught!" << std::endl;
            return 1;
        }
        
        mfc.deallocate(valid_ptr);
        std::cout << "[+] Verification SUCCESS: All C++ container boundary tests passed." << std::endl;
        return 0;
        
    } catch (const std::exception& e) {
        std::cerr << "[-] Exception occurred during test execution: " << e.what() << std::endl;
        return 1;
    }
}
