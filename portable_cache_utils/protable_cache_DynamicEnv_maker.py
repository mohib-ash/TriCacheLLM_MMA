# File: portable_cache_utils/protable_cache_DynamicEnv_maker.py
import os
from pathlib import Path
from typing import Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from portable_cache_dbSchema import Paths
from sqlalchemy import create_engine, select
from pydantic import model_validator



_INTERNAL_ENV_FILE = (
    Path.cwd()
    / ".portable_cache_internal"
    / ".env_protable_cache"
)

class Settings(BaseSettings):
    portable_cache_embedding_model: str = "all-MiniLM-L6-v2"
    portable_cache_chroma_db_dir: str = "./chroma_cache_storage"
    portable_cache_cohere_rerank_model: str = "rerank-english-v3.0"
    portable_cache_cohere_api_key: Optional[str] = None 
    portable_cache_redis_url: str = "redis://localhost:6379/0" 
    portable_cache_registry_db: Optional[str] = None
    portable_cache_cache_proj_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    
    @model_validator(mode="after")
    def assemble_registry_path(self) -> "Settings":
        if not self.portable_cache_registry_db:
            self.portable_cache_registry_db = str(
                Path(self.portable_cache_chroma_db_dir) / "registry.db"
            )
        return self

    model_config = SettingsConfigDict(
            env_file=_INTERNAL_ENV_FILE if _INTERNAL_ENV_FILE.exists() else (
                Path(__file__).resolve().parent.parent
                    / ".portable_cache_internal"
                    / ".env_protable_cache"
            ),  
            env_file_encoding="utf-8",
            extra="ignore"
        )

def get_settings() -> Settings:
    """Always returns a fresh Settings instance loaded from current environment / file."""
    return Settings()


def system_key(redis_url: str, cohere_api_key: str, chroma_db_dir: str = "./chroma_cache_storage", local_cache_dir: Path = None, portable_cache_registry_db: Path = None) -> dict[str, Any]:
    if not redis_url or not cohere_api_key:
        raise ValueError("'redis_url' and 'cohere_api_key' are strictly required to initialize the cache system.")

    try:
        resolved_chroma = str(Path(chroma_db_dir).resolve())
        
        if local_cache_dir:
            cache_dir = Path(local_cache_dir)
        else:
            cache_dir = (Path.cwd() / ".portable_cache_internal")
            
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        if portable_cache_registry_db:
            db_file = portable_cache_registry_db.resolve()
        else:
            db_file = cache_dir / "registry.db"
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        resolved_db_path = str(db_file)
        env_file = cache_dir / ".env_protable_cache"

        env_lines = []
        if env_file.exists():
            env_lines = env_file.read_text().splitlines()

        configs = {
            "PORTABLE_CACHE_REDIS_URL": redis_url,
            "PORTABLE_CACHE_COHERE_API_KEY": cohere_api_key,
            "PORTABLE_CACHE_CHROMA_DB_DIR": resolved_chroma,
            "PORTABLE_CACHE_REGISTRY_DB": resolved_db_path
        }

        for key, val in configs.items():
            os.environ[key] = val
            updated = False
            for i, line in enumerate(env_lines):
                if line.startswith(f"{key}="):
                    env_lines[i] = f"{key}={val}"
                    updated = True
                    break
            if not updated:
                env_lines.append(f"{key}={val}")

        env_file.write_text("\n".join(env_lines) + "\n")

        sync_engine = create_engine(f"sqlite:///{db_file}")
        from sqlalchemy.orm import sessionmaker
        SessionLocal = sessionmaker(bind=sync_engine)
        
        with SessionLocal() as session:
            path_record = session.query(Paths).filter_by(id=1).first()
            if path_record:
                path_record.portable_env_path = str(env_file.resolve())
                path_record.chroma_db_dir = resolved_chroma
                path_record.redis_url = redis_url
                path_record.db_path = resolved_db_path
                path_record.cohere_api_key = cohere_api_key  
            else:
                path_record = Paths(
                    id=1,
                    portable_env_path=str(env_file.resolve()),
                    chroma_db_dir=resolved_chroma,
                    redis_url=redis_url,
                    db_path=resolved_db_path,
                    cohere_api_key=cohere_api_key  
                )
                session.add(path_record)
            session.commit()
        
        print("Portable Cache initialized, saved to .env, and recorded in registry Paths table successfully!")
        return {"success": True, "configured": list(configs.keys())}
        
    except Exception as e:
        print(f"Error initializing Portable Cache configuration: {str(e)}")
        return {"success": False, "error": str(e), "configured": []}


async def get_stored_paths() -> dict:
    """Instantly pulls absolute paths and credentials from SQLite,
    immune to working directory changes.
    """
    from portable_cache_schemas.portable_cache_dbConf import db_manager

    async with db_manager.async_session() as session:
        result = await session.execute(
            select(Paths).filter_by(id=1)
        )
        record = result.scalars().first()

        if not record:
            raise RuntimeError(
                "Portable Cache not initialized! Run system_key() first."
            )

        return {
            "env_path": record.portable_env_path,
            "chroma_dir": record.chroma_db_dir,
            "redis_url": record.redis_url,
            "db_path": record.db_path,
            "cohere_api_key": record.cohere_api_key,
        }