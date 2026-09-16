# File: portable_cache_bgWorkers/portable_cache_celery_conf.py
from pathlib import Path
from celery import Celery
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings
from ..portable_cache_dbSchema import Paths

def get_celery_redis_url() -> str:
    """Sneaks a peek into the SQLite registry DB to grab the true source-of-truth redis_url for Celery."""
    try:
        settings = get_settings()
        if settings.protable_cache_registry_db:
            db_path = Path(settings.protable_cache_registry_db)
            if db_path.exists():
                sync_engine = create_engine(f"sqlite:///{db_path}")
                SessionLocal = sessionmaker(bind=sync_engine)
                with SessionLocal() as session:
                    path_record = session.query(Paths).filter_by(id=1).first()
                sync_engine.dispose()
                if path_record and path_record.redis_url:
                    return path_record.redis_url
    except Exception:
        pass
    
    return get_settings().portable_cache_redis_url

redis_base_url = get_celery_redis_url()

celery_app = Celery(
    "fastapi_ai_backend",
    broker=redis_base_url,
    backend=redis_base_url.rsplit("/", 1)[0] + "/1",
)

celery_app.conf.imports = (
    "TriCacheLLM_MMA.portable_cache_bgWorkers.portable_cache_workers",
)

celery_app.conf.task_default_queue = "default"
celery_app.conf.task_routes = {
    "ai.*": { 
        "queue": "ai"
    },
    "retri.*": {  
        "queue": "retri"
    },
    "maintenance.*": {
        "queue": "maintenance"
    },
}