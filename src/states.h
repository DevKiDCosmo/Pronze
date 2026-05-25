

class RAMAccessBehaviour
{
    public:
    // ============================================================
    // Defines for States of Memory for Direct
    // ============================================================
    #define NORMAL         0x00 // Just Normal

    #define BADACCESS      0x01 // Bad reading
    #define BADWRITE       0x02 // Bad writing
        #define BADACCR_PROFILER 0x101 // Bad read in profiler or outside safe corrupt zone.
        #define BADACCW_PROFILER 0x102 // Bad write in profiler or outside safe corrupt zone.

    #define OVERALLOCATING 0x03 // When exceeding designated PAGE or Allocation HEAP
    #define SUBALLOCATING  0x04 // When allocating out of PAGE or allocation heap

    #define WRONGTYPE 0x05 //
        #define WRONGTYPE_IC 0x205 // Int to Char not possible
        #define WRONGTYPE_IS 0x206 // Int to String not possible
    
    #define ALLOCATION_CORRUPTIONB 0x06 // Corrupted mem before allocation
    #define ALLOCATION_CORRUPTIONW 0x07 // Corrupted mem while allocation
    #define ALLOCATION_CORRUPTIONA 0x08 // Corrupted mem after deallocation


    // ============================================================
    // Defines for States of Memory for Sim
    // ============================================================

    // ============================================================
    // Defines for States of Memory for Escape out of Dedicated
    // Space
    // ============================================================
    #define CRITICALACC 0
    #define FATALACC 1
};
