#include <linux/gfp.h>
#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/slab.h>
#include <linux/list.h>
#include <linux/mutex.h>

#include "_page_pr.h"

/* Structure to track each allocation */
struct page_node {
    unsigned long address;
    int order;
    struct list_head list;
};

/* Dynamic tracking structures (behaves like std::vector internally) */
static LIST_HEAD(pages_list);
static DEFINE_MUTEX(pages_mutex);

int __page_allocation_function(int order)
{
    unsigned long addr;
    struct page_node *new_node;

    pr_info("PageAllocator: allocating order-%d pages\n", order);

    addr = __get_free_pages(GFP_KERNEL, order);
    if (!addr) {
        pr_err("PageAllocator: failed to allocate order-%d pages\n", order);
        return -ENOMEM;
    }

    new_node = kmalloc(sizeof(*new_node), GFP_KERNEL);
    if (!new_node) {
        pr_err("PageAllocator: failed to allocate tracking node\n");
        free_pages(addr, order);
        return -ENOMEM;
    }

    new_node->address = addr;
    new_node->order = order;
    INIT_LIST_HEAD(&new_node->list);

    mutex_lock(&pages_mutex);
    /* Add to head so that the last allocated page is always at the front */
    list_add(&new_node->list, &pages_list);
    mutex_unlock(&pages_mutex);

    pr_info("PageAllocator: successfully allocated order-%d pages at 0x%lx\n", order, addr);
    return 0;
}

int *__page_aladdr(void)
{
    struct page_node *first;
    int *res = NULL;

    mutex_lock(&pages_mutex);
    if (!list_empty(&pages_list)) {
        first = list_first_entry(&pages_list, struct page_node, list);
        res = (int *)first->address;
    }
    mutex_unlock(&pages_mutex);

    return res;
}

int page_pr_allocate(int order)
{
    return __page_allocation_function(order);
}

int page_pr_allocate_at(int order, int *addr)
{
    int ret;
    int *actual_addr;

    pr_info("PageAllocator: requested allocation of order-%d pages at hint %p\n", order, addr);
    
    ret = __page_allocation_function(order);
    if (ret == 0) {
        actual_addr = __page_aladdr();
        pr_info("PageAllocator: allocated pages at %p (hint was %p)\n", actual_addr, addr);
    }
    return ret;
}

int *page_pr_last_address(void)
{
    return __page_aladdr();
}

int page_pr_free(int *addr)
{
    struct page_node *curr, *tmp;
    unsigned long target_addr = (unsigned long)addr;
    unsigned long found_addr = 0;
    int found_order = 0;
    int found = 0;

    pr_info("PageAllocator: deallocation request for address %p\n", addr);

    mutex_lock(&pages_mutex);
    list_for_each_entry_safe(curr, tmp, &pages_list, list) {
        if (curr->address == target_addr) {
            list_del(&curr->list);
            found_addr = curr->address;
            found_order = curr->order;
            kfree(curr);
            found = 1;
            break;
        }
    }
    mutex_unlock(&pages_mutex);

    if (found) {
        free_pages(found_addr, found_order);
        pr_info("PageAllocator: successfully freed order-%d pages at 0x%lx\n", found_order, found_addr);
        return 0;
    }

    pr_err("PageAllocator: address %p not found in active tracking list\n", addr);
    return -EINVAL;
}

int page_pr_restrict(int *addr)
{
    pr_info("PageAllocator: restricting page access permissions at %p (SIMULATED)\n", addr);
    /* In a real kernel, page permissions could be adjusted (e.g. set_memory_ro) */
    return 0;
}

void page_pr_cleanup(void)
{
    struct page_node *curr, *tmp;

    pr_info("PageAllocator: starting cleanup of all tracked pages\n");

    mutex_lock(&pages_mutex);
    list_for_each_entry_safe(curr, tmp, &pages_list, list) {
        list_del(&curr->list);
        pr_info("PageAllocator: freeing page at 0x%lx (order-%d) during cleanup\n", curr->address, curr->order);
        free_pages(curr->address, curr->order);
        kfree(curr);
    }
    mutex_unlock(&pages_mutex);
}