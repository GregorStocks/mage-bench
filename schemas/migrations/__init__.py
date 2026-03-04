"""Migration module registry.

Each module defines SOURCE_VERSION, TARGET_VERSION, up(), and down().
The MIGRATIONS list must be ordered by SOURCE_VERSION (ascending).
"""

from schemas.migrations import v2_to_v3, v3_to_v4, v4_to_v5, v5_to_v6

MIGRATIONS = [
    v2_to_v3,
    v3_to_v4,
    v4_to_v5,
    v5_to_v6,
]
