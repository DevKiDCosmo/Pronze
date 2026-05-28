# Pager

Short documentation for the pager helper functions (pager subsystem).

Functions
- `__page_allocation_function(int order)`: Allocate pages of size 2^order; returns `0` on success or a negative error code on failure.
- `__page_aladdr(void)`: Returns an `int *` pointing to the most recently allocated page (or `NULL` if none).
- `page_pr_allocate(int order)`: C wrapper for `__page_allocation_function`.
- `page_pr_allocate_at(int order, int *addr)`: Attempts allocation with a hint address `addr`; logs the actually used address.
- `page_pr_last_address(void)`: Wrapper that returns the last known allocated address.
- `page_pr_free(int *addr)`: Frees a previously tracked page; returns `0` on success or `-EINVAL` if the address was not found.
- `page_pr_restrict(int *addr)`: Simulates restricting page access permissions (no real memory protection applied).
- `page_pr_cleanup(void)`: Cleans up and frees all tracked pages.

Design philosophy
- Small, test-friendly C API. All allocations are tracked centrally (`list_head` + `mutex`) so tests, simulations and controlled fault injection can be performed without introducing heavy kernel-ABI or runtime complexity.

