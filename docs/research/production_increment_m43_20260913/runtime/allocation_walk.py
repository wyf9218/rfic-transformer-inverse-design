"""Fresh fd-relative allocation walk; no cached totals or symlink traversal."""
import os
import stat

import controlled_metadata as m
import start_slots as slots


def allocated_snapshot(root, candidate_roots):
    root = slots.path_ok(root)
    indexed = {}
    totals = {rid: 0 for rid in candidate_roots}
    seen = {rid: set() for rid in candidate_roots}
    total_seen = set()
    total = 0
    for rid, value in candidate_roots.items():
        base = slots.path_ok(value)
        m.require(base.is_relative_to(root) and base != root, 'CLAIM_OUTSIDE_BUDGET_ROOT')
        key = base.relative_to(root).parts
        m.require(key not in indexed, 'DUPLICATE_CLAIM_ROOT')
        indexed[key] = rid

    def account(info, owners):
        nonlocal total
        m.require(not stat.S_ISLNK(info.st_mode), 'BUDGET_TREE_SYMLINK_FORBIDDEN')
        inode = (info.st_dev, info.st_ino)
        size = info.st_blocks * 512
        if inode not in total_seen:
            total_seen.add(inode)
            total += size
        for rid in owners:
            if inode not in seen[rid]:
                seen[rid].add(inode)
                totals[rid] += size

    # scandir retains DirEntry metadata, avoiding repeated Path/lstat/relative_to
    # work for every entry. Every call still freshly visits the entire root.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def visit(fd, relative, owners, expected=None):
        info = os.fstat(fd)
        if expected is not None:
            m.require((info.st_dev, info.st_ino) == expected, 'BUDGET_DIRECTORY_CHANGED_DURING_WALK')
        account(info, owners)
        with os.scandir(fd) as entries:
            for entry in entries:
                try:
                    value = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                m.require(not stat.S_ISLNK(value.st_mode), 'BUDGET_TREE_SYMLINK_FORBIDDEN')
                parts = relative + (entry.name,)
                child_owners = owners + (indexed[parts],) if parts in indexed else owners
                if stat.S_ISDIR(value.st_mode):
                    # Refuse a directory swapped for a symlink between stat/open.
                    child = os.open(entry.name, flags, dir_fd=fd)
                    try:
                        visit(child, parts, child_owners, (value.st_dev, value.st_ino))
                    finally:
                        os.close(child)
                else:
                    account(value, child_owners)

    fd = os.open(root, flags)
    try:
        visit(fd, (), ())
    finally:
        os.close(fd)
    return total, totals
