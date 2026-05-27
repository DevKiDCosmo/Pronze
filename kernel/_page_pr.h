#ifndef PAGE_PR_H
#define PAGE_PR_H

/* Allocate a page of order 2^order. Returns 0 on success, negative error code on failure. */
int __page_allocation_function(int order);

/* Retrieve the address of the last allocated page. */
int *__page_aladdr(void);

/* C helpers */
int page_pr_allocate(int order);
int page_pr_allocate_at(int order, int *addr);
int *page_pr_last_address(void);
int page_pr_free(int *addr);
int page_pr_restrict(int *addr);

/* Module unload cleanup helper to prevent memory leaks */
void page_pr_cleanup(void);

#endif /* PAGE_PR_H */