# File: portable_cache_schemas/portable_cache_schemas.py

import enum
class CacheVDBStatus(enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"