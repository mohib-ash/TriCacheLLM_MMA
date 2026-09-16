# File: portable_cache_bgWorkers/portable_cache_workers.py
import asyncio
from pathlib import Path
from typing import Any
from ..portable_cache_bgWorkers.portable_cache_celery_conf import celery_app
from ..portable_cache_schemas.portable_cache_dbConf import db_manager
import json
from ..portable_cache_dbSchema import CacheVDBResource
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings
from ..portable_cache_schemas.portable_cache_schemas import CacheVDBStatus 
from langchain_core.documents import Document as LangChainDocument
from sqlalchemy import select
from ..portable_cache_utils.portable_cache_embedding_model import embedding_model
from langchain_chroma import Chroma
from datetime import datetime, timezone
from ..portable_cache_dbSchema import Paths
from sqlalchemy import text
import time

@celery_app.task(bind=True, max_retries=3, name="ai.cache_vdb")
def create_cache_vdb_worker(self, user_id: int):
    try:
        return asyncio.run(
            create_cache_vdb_async(
                task_instance=self,
                user_id=user_id,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)

async def create_cache_vdb_async(task_instance: Any, user_id: int):
    async with db_manager.celery_session() as db:
        try:
            result = await db.execute(select(Paths).filter_by(id=1))
            path_record = result.scalars().first()
            
            if path_record and path_record.chroma_db_dir:
                base_chroma_dir = path_record.chroma_db_dir
            else:
                base_chroma_dir = get_settings().portable_cache_chroma_db_dir
                
            user_cache_dir = Path(base_chroma_dir) / f"user_{user_id}"
            await asyncio.to_thread(
                user_cache_dir.mkdir,
                parents=True,
                exist_ok=True,
            )

            await asyncio.to_thread(
                lambda: Chroma(
                    collection_name=f"question_cache_{user_id}",
                    embedding_function=embedding_model,
                    persist_directory=str(user_cache_dir),
                )
            )

            stmt = select(CacheVDBResource).where(
                CacheVDBResource.user_id == user_id
            )
            result = await db.execute(stmt)
            cache_res = result.scalar_one_or_none()

            if cache_res is None:
                raise RuntimeError(
                    f"Cache VDB resource record not found in DB for user {user_id}"
                )

            cache_res.status = CacheVDBStatus.READY
            cache_res.vdb_path = str(user_cache_dir)
            cache_res.failure_reason = None
            await db.commit()

            return {
                "status": "READY",
                "user_id": user_id,
            }

        except Exception as exc:
            stmt = select(CacheVDBResource).where(
                CacheVDBResource.user_id == user_id
            )
            result = await db.execute(stmt)
            cache_res = result.scalar_one_or_none()

            if cache_res:
                cache_res.status = CacheVDBStatus.FAILED
                cache_res.failure_reason = str(exc)
                await db.commit()

            raise


@celery_app.task(bind=True, max_retries=3, name="ai.push_cache_vdb")
def push_responce_in_cache_worker(self, user_id: int, question: str, model_output_dict: dict):
    try:
        return asyncio.run(
            push_response_in_cache_async(
                task_instance=self,
                user_id=user_id,
                question=question,
                model_output_dict=model_output_dict
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=10)

async def push_response_in_cache_async(task_instance: Any, user_id, question: str, model_output_dict: dict) -> str | None:
    print(f"CACHE_VDB_PUSH_ASYNC_ENTERED where user_id: {user_id}")
    async with db_manager.celery_session() as db:
        try:
            response_json_str = json.dumps(model_output_dict)

            cache_metadata = {
                "user_id": user_id,
                "question": question,
                "llm_response": response_json_str,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "timestamp": time.time()
            }

            cache_document = LangChainDocument(
                page_content=question,
                metadata=cache_metadata
            )

            stmt = select(CacheVDBResource).where(
                CacheVDBResource.user_id == user_id
            )

            result = await db.execute(stmt)
            cache_res = result.scalar_one_or_none()

            if cache_res is None:
                raise RuntimeError(
                    f"Cache VDB resource record not found for user {user_id}"
                )

            if not cache_res.vdb_path:
                raise RuntimeError(
                    f"Cache VDB path not found for user {user_id}"
                )

            user_cache_vdb_path = Path(cache_res.vdb_path)

            user_cache_vdb = await asyncio.to_thread(
                lambda: Chroma(
                    collection_name=f"question_cache_{user_id}",
                    embedding_function=embedding_model,
                    persist_directory=str(user_cache_vdb_path),
                )
            )

            await asyncio.to_thread(
                user_cache_vdb.add_documents,
                documents=[cache_document]
            )

            return cache_metadata["created_at"]

        except Exception as exc:
            print(f"AI_SERVICE_FAILED, ---WARNING--- | user_id: {user_id} and error: {str(exc)}")
            raise