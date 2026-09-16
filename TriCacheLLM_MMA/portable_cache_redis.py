# File: portable_cache_redis.py
from pathlib import Path
import redis.asyncio as aioredis
from typing import Optional
from .portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings
from .portable_cache_dbSchema import Paths
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

class RedisManager:
    """Encapsulates the Redis connection pool without relying on unsafe global state."""
    
    def __init__(self):
        self._pool: Optional[aioredis.ConnectionPool] = None
        self._current_url: Optional[str] = None
    
    def _get_redis_url_from_registry(self) -> Optional[str]:
        """Sneaks a peek into the SQLite registry DB to grab the true source-of-truth redis_url."""
        try:
            settings = get_settings()
            db_path = Path(settings.portable_cache_registry_db)
            if not db_path.exists():
                return None
            
            sync_engine = create_engine(f"sqlite:///{db_path}")
            SessionLocal = sessionmaker(bind=sync_engine)
            with SessionLocal() as session:
                path_record = session.query(Paths).filter_by(id=1).first()
            
            sync_engine.dispose()
            if path_record and path_record.redis_url:
                return path_record.redis_url
        except Exception:
            pass
        return None

    async def get_client(self, redis_url: Optional[str] = None) -> aioredis.Redis:
        """Lazily initializes and returns a connection-pooled Redis client."""
        settings = get_settings()
        
        target_url = redis_url or self._get_redis_url_from_registry() or settings.portable_cache_redis_url
        
        if self._pool is None or self._current_url != target_url:
            if self._pool:
                await self._pool.disconnect()
            
            self._current_url = target_url
            self._pool = aioredis.ConnectionPool.from_url(
                target_url,
                decode_responses=False #false coz vectors are stored in bytes!
            )
        
        return aioredis.Redis(connection_pool=self._pool)

    async def close_redis_pool(self):
        """Clean shutdown for app teardown/lifespan events."""
        if self._pool:
            await self._pool.disconnect()
            self._pool = None

redis_manager = RedisManager()

async def get_redis(redis_url: Optional[str] = None) -> aioredis.Redis:
    """Helper function to cleanly fetch the client."""
    return await redis_manager.get_client(redis_url=redis_url)