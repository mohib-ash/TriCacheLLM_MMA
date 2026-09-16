# File: portable_cache_main.py
import asyncio
from asyncio import subprocess
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from .portable_cache_schemas.portable_cache_dbConf import init_cache_database, init_db_tables, db_manager
from .portable_cache_schemas.portable_cache_schemas import CacheVDBStatus
from .portable_cache_dbSchema import CacheVDBResource
from sqlalchemy import select
from .portable_cache_bgWorkers.portable_cache_workers import create_cache_vdb_worker, push_responce_in_cache_worker
from .portable_cache_utils.portable_cache_embedding_model import embedding_model
from redis.asyncio import Redis
from redis.commands.search.field import TagField, VectorField
from redis.commands.search.index_definition import (
    IndexDefinition,
    IndexType,
)
from redis.commands.search.query import Query
from redis.exceptions import ResponseError
from pydantic import BaseModel
import numpy as np
from langchain_chroma import Chroma
from langchain_core.documents import Document as LangChainDocument
import random
from .portable_cache_Ai.portable_cache_rerankAi import portable_cache_cohere_rerank
from typing import Optional
from .portable_cache_redis import get_redis 
from .portable_cache_redis import redis_manager
from .portable_cache_utils.protable_cache_DynamicEnv_maker import system_key
from .portable_cache_dbSchema import Paths
import sys


def create_vector_index_schema(dim: int, distance_metric: str = "COSINE", m: int = 16, ef_construction: int = 200, ef_runtime: int = 10) -> list:
    return [
        VectorField(
            "vector",
            "HNSW",
            {
                "TYPE": "FLOAT32",
                "DIM": dim,
                "DISTANCE_METRIC": distance_metric,
                "M": m,
                "EF_CONSTRUCTION": ef_construction,
                "EF_RUNTIME": ef_runtime
            }
        ),
        TagField("scope"),
    ]

async def generate_embedding(text: str) -> list[float]:
    return await asyncio.to_thread(embedding_model.embed_query, text)

def generate_cache_key(question: str, user_id: Any = 0) -> str:
    normalized_q = question.strip().lower()
    payload = f"{user_id}:{normalized_q}"
    hash_sig = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"ai_hot_cache:{user_id}:{hash_sig}"

async def create_cache_vdb_inishiator(user_id, db) -> dict[str, Any] | None:
    stmt = select(CacheVDBResource).where(
        CacheVDBResource.user_id == user_id,
    )
    
    result = await db.execute(stmt)
    cache_resource = result.scalar_one_or_none()
    
    if cache_resource is None:
        print(f"CACHE_VDB_CREATING_PENDING where user_id: {user_id}")
        cache_resource = CacheVDBResource(
            user_id=user_id,
            status=CacheVDBStatus.PENDING,
            version=0,
        )
        db.add(cache_resource)
        await db.commit()
        await db.refresh(cache_resource)
    
    proceed = cache_resource.status in (
        CacheVDBStatus.PENDING,
        CacheVDBStatus.FAILED,
    )
    
    if not proceed:
        print(f"CACHE_VDB_STATUS_SKIPPED where user_id: {user_id}")
        return None
    
    print(f"CACHE_VDB_LOCKED_PROCESSING where user_id: {user_id}")
    cache_resource.status = CacheVDBStatus.PROCESSING
    await db.commit()
    await db.refresh(cache_resource)

    print(f"CACHE_VDB_WORKER_FIRED where user_id: {user_id}")
    task_id = create_cache_vdb_worker.delay(user_id=user_id)

    data: dict = {
        "success": True,
        "task_id": task_id.id if hasattr(task_id, "id") else str(task_id) if task_id else None,
        "status": "PROCESSING"
    }
    return data

async def create_redis_vector_index(user_id, redis_client: Redis, dim: int = 384):
    flag_key = f"meta:index_initialized:{user_id}" 
    index_name = f"idx:user_cache:{user_id}"
    print(f"REDIS_INDEX_CHECK_STARTED where user_id: {user_id}")
    
    if await redis_client.exists(flag_key):
        try:
            await redis_client.ft(index_name).info()
            print(f"REDIS_INDEX_EXISTS where user_id: {user_id}")
            return
        except ResponseError:
            print(f"REDIS_INDEX_FLAG_STALE where user_id: {user_id}")
    
    
    schema = (
        VectorField(
            "vector",
            "HNSW",
            {
                "TYPE": "FLOAT32",
                "DIM": dim,
                "DISTANCE_METRIC": "COSINE",
                "M": 16,
                "EF_CONSTRUCTION": 200,
                "EF_RUNTIME": 10
            }
        ),
        TagField("scope"), 
    )
    
    try:
        print(f"REDIS_INDEX_CREATION_STARTED where user_id: {user_id}")
        await redis_client.ft(index_name).create_index( 
            schema, 
            definition=IndexDefinition(
                prefix=[f"cache:item:{user_id}:"],  
                index_type=IndexType.HASH 
            )
        )
        print(f"REDIS_INDEX_CREATED_SUCCESS where user_id: {user_id}")
    except ResponseError as e:
        if "Index already exists" not in str(e):
            print(f"REDIS_INDEX_CREATION_ERROR where error: {str(e)} and user_id: {user_id}")
            raise e
        print(f"REDIS_INDEX_ALREADY_EXISTS where user_id: {user_id}")
        
    await redis_client.set(flag_key, "1") 
    print(f"REDIS_INDEX_INITIALIZATION_COMPLETE where user_id: {user_id}")
    
def portable_cache_normalize_scope(scope: Any = None) -> str:
    if scope is None:
        return "global"
    if isinstance(scope, list):
        return "__".join(sorted(str(s).strip().lower() for s in scope))
    if isinstance(scope, tuple):
        return "__".join(sorted(str(s).strip().lower() for s in scope))
    return str(scope).strip().lower()

def escape_redis_tag(value: str) -> str:
    special_chars = r",.<>{}[]\"':;!@#$%^&*()-+=~|"
    return "".join(
        f"\\{char}" if char in special_chars or char == " " else char
        for char in value
    )
    
async def save_to_redis_vector_cache(user_id, 
                                    question: str, 
                                    response_payload: dict | BaseModel | Any, 
                                    redis_client: Redis, 
                                    scope_input: Any = None
                                    ): 
    print(f"REDIS_VECTOR_SAVE_STARTED where user_id: {user_id}")
    try:
        await create_redis_vector_index(user_id=user_id, redis_client=redis_client)
        print(f"REDIS_VECTOR_INDEX_ENSURED where user_id: {user_id}")
        
        vector = await generate_embedding(question)
        print(f"REDIS_VECTOR_EMBEDDING_GENERATED where user_id: {user_id}")
        vector_bytes = np.array(vector, dtype=np.float32).tobytes()
        
        if isinstance(response_payload, BaseModel):
            payload_data = response_payload.model_dump_json()
        else:
            payload_data = json.dumps(response_payload)
        print(f"REDIS_VECTOR_PAYLOAD_SERIALIZED where user_id: {user_id}")

        normalized_question = question.strip().lower()
        scope = portable_cache_normalize_scope(scope_input)
        
        scope_payload = f"{normalized_question}:{scope}"
        entry_id = hashlib.sha256(scope_payload.encode("utf-8")).hexdigest()
        redis_key = f"cache:item:{user_id}:{entry_id}"

        await redis_client.hset(
            redis_key,
            mapping={
                "vector": vector_bytes,
                "response_data": payload_data,
                "scope": scope,
            }
        )
        print(f"REDIS_VECTOR_HASH_SAVED where user_id: {user_id}")
        
        await asyncio.create_task(redis_client.expire(redis_key, 86400))
        print(f"REDIS_VECTOR_TTL_SET where user_id: {user_id}")

    except Exception as e:
        print(f"REDIS_VECTOR_SAVE_ERROR where error: {str(e)} and user_id: {user_id}")
        raise

async def get_user_cache_vdb(user_id, db) -> Chroma | None:
    print(f"CHECKING_USER_VDB where user_id: {user_id}")
    
    stmt = select(CacheVDBResource).where(CacheVDBResource.user_id == user_id)
    result = await db.execute(stmt)
    cache_res = result.scalar_one_or_none()
    
    if cache_res is None or cache_res.status != CacheVDBStatus.READY or not cache_res.vdb_path:
        print(f"VDB_NOT_FOUND where user_id: {user_id}")
        return None
    
    print(f"VDB_FOUND where user_id: {user_id}")
    
    return await asyncio.to_thread(
        lambda: Chroma(
            persist_directory=cache_res.vdb_path,
            collection_name=f"question_cache_{user_id}",
            embedding_function=embedding_model,
        )
    )

async def get_cached(question: str, cache_vdb: Chroma, user_id) -> dict[str, Any]:
    print(f"CHROMA_SIMILARITY_SEARCH_STARTED where user_id: {user_id}")
    try:
        fetched_and_scores: list[tuple[LangChainDocument, float]] = await asyncio.to_thread(
            cache_vdb.similarity_search_with_score,
            query=question,
            k=10
        ) 
    except Exception as exc:
        print(f"CHROMA_SIMILARITY_SEARCH_ERROR where error: {str(exc)} and user_id: {user_id}")
        return {"success": False, "data": None}

    if not fetched_and_scores:
        print(f"CHROMA_CANDIDATES_NOT_FOUND where user_id: {user_id}")
        return {"success": False, "data": None}

    documents_for_rerank = [
        LangChainDocument(
            page_content=(
                f"Cached Question:\n{doc.metadata.get('question')}\n\n"
                f"Cached Answer:\n{doc.metadata.get('llm_response')}"
            ),
            metadata=doc.metadata
        )
        for doc, score in fetched_and_scores
    ]

    print(f"CHROMA_RERANK_STARTED where user_id: {user_id}")
    rerank_response: dict = await portable_cache_cohere_rerank(
        question=question,
        user_id=user_id,
        received_docs=documents_for_rerank,
        top_k=3
    )

    if not rerank_response["success"]:
        print(f"CHROMA_RERANK_FAILED where user_id: {user_id}")
        return {"success": False, "data": None}

    best_doc = rerank_response["data"][0]
    best_score = best_doc.metadata.get("rerank_score", 0.0) 

    RELEVANCE_THRESHOLD = 0.85
    DELIBERATE_MISS_RATE = 0.10
    
    if best_score < RELEVANCE_THRESHOLD:
        print(f"CHROMA_RELEVANCE_THRESHOLD_FAILED where user_id: {user_id}")
        return {"success": False, "data": None}

    if random.random() < DELIBERATE_MISS_RATE:
        print(f"CHROMA_DELIBERATE_MISS_TRIGGERED where user_id: {user_id}")
        return {"success": False, "data": None}

    print(f"CHROMA_CACHE_HIT where user_id: {user_id}")
    raw_response = best_doc.metadata.get("full_llm_response") or best_doc.metadata.get("llm_response")
    parsed_answer = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
    
    return {"success": True, "data": parsed_answer}
    
async def get_redis_vector_cached(question: str, user_id, redis_client: Redis, top_k: int = 1, scope: Any = None) -> dict[str, Any]:
    print(f"REDIS_VECTOR_SEARCH_STARTED where user_id: {user_id}")
    try:
        print(f"REDIS_VECTOR_GENERATING_EMBEDDING where user_id: {user_id}")
        question_vector: list[float] = await generate_embedding(question) 
        
        vector_bytes = np.array(question_vector, dtype=np.float32).tobytes()
        index_name = f"idx:user_cache:{user_id}"
        
        normalized_scope: str = portable_cache_normalize_scope(scope) 
        print(f"REDIS_VECTOR_SCOPE_NORMALIZED where user_id: {user_id}")
        
        escaped_scope = escape_redis_tag(normalized_scope)
        query_str = (
            f"(@scope:{{{escaped_scope}}})"
            f"=>[KNN {top_k} @vector $vec AS vector_score]"
        )

        query = (
            Query(query_str)
            .return_fields("response_data", "vector_score",) 
            .sort_by("vector_score") 
            .dialect(2) 
        )
        
        print(f"REDIS_VECTOR_QUERY_EXECUTING where user_id: {user_id}")
        results = await redis_client.ft(index_name).search( 
            query, 
            query_params={"vec": vector_bytes}
        )
        
        if results and results.docs:
            doc = results.docs[0]
            
            score = float(doc.vector_score)
            if score > 0.20:
                # print(f"REDIS_VECTOR_SCORE where user_id: {user_id} "f"score={score}")
                print(f"REDIS_VECTOR_SCORE_MISMATCH where user_id: {user_id}")
                return {"success": False, "data": None}



            print(f"REDIS_VECTOR_HIT_FOUND where user_id: {user_id}")
            return {"success": True, "data": json.loads(doc.response_data)}
                
        print(f"REDIS_VECTOR_CACHE_MISS user_id: {user_id}")
        return {"success": False, "data": None}

    except Exception as e:
        print(f"REDIS_VECTOR_ERROR where error: {str(e)} and user_id: {user_id}")
        return {"success": False, "data": None}

async def multi_tier_cache_system(question: str, user_id, normal_redis_instance: Redis, db, cache_key: str, scope: Any = None, top_k: int = 1) -> dict[str, Any]:
    #Tier 1:
    cached_payload = await normal_redis_instance.get(cache_key)
    if cached_payload:
        print(f"CACHE_TIER1_EXACT_HIT where user_id: {user_id}")
        return {"success": True, "data": json.loads(cached_payload)}
    
    #Tier 2:
    print(f"CACHE_POLICY_CACHEABLE where user_id: {user_id}")
    redis_vector_result: dict = await get_redis_vector_cached(question=question, user_id=user_id, redis_client=normal_redis_instance, scope=scope, top_k=top_k)
    
    if redis_vector_result["success"]:
        print(f"CACHE_TIER2_VECTOR_HIT where user_id: {user_id}")
        asyncio.create_task(
            normal_redis_instance.setex(cache_key, 86400, json.dumps(redis_vector_result["data"]))
        )
        return {"success": True, "data": redis_vector_result["data"]}


    #Tier 3:
    cache_vdb: Chroma | None = await get_user_cache_vdb(user_id=user_id, db=db)
    if cache_vdb is not None:
        chroma_result: dict = await get_cached(question=question, cache_vdb=cache_vdb, user_id=user_id)
        
        if chroma_result["success"]:
            print(f"CACHE_TIER3_CHROMA_HIT where user_id: {user_id}")
            async def run_both():
                await asyncio.gather(
                    normal_redis_instance.setex(cache_key, 86400, json.dumps(chroma_result["data"])), 
                    save_to_redis_vector_cache(
                        user_id=user_id,
                        question=question,
                        response_payload=chroma_result["data"],
                        redis_client=normal_redis_instance,
                        scope_input=scope,
                    ), 
                    return_exceptions=True 
                )

            asyncio.create_task(run_both()) 
            return {"data": chroma_result["data"], "success": True}
        else: 
            print(f"CACHE_SYSTEM_MISS_FALLBACK where user_id: {user_id}")
            return {"success": False, "data": None}
    else:
        print(f"CACHE_VDB_NOT_FOUND where user_id: {user_id}")
        return {"success": False, "data": None}

async def push_responce_in_cache_inishiator(model_output: Any, question: str, user_id) -> dict:
    model_dict = model_output.model_dump() if hasattr(model_output, "model_dump") else model_output

    task = push_responce_in_cache_worker.delay(
        user_id=user_id,
        question=question,
        model_output_dict=model_dict,
    )
    return {
        "success": True,
        "task_id": task.id if hasattr(task, "id") else str(task.id),
        "status": "PROCESSING"
    }






async def create_cache_system(
    redis_url: Optional[str] = None, 
    cohere_api_key: Optional[str] = None, 
    chroma_db_dir: Optional[str] = None, 
    db_path: Optional[str] = None, 
    user_id: Any = 0
) -> dict[str, bool | str] | None:
    
    print(f"CACHE_SYSTEM_INIT_STARTED where user_id: {user_id}")
    try:        
        if user_id == 0:
            if not all([
                redis_url,
                cohere_api_key,
                chroma_db_dir,
                db_path,
            ]):
                raise ValueError(
                    "System initialization requires redis_url, "
                    "cohere_api_key, chroma_db_dir and db_path."
                )
                
            #these are to be made for the first time    
            db_file = Path(db_path)
            init_cache_database(db_file) #this creates all session mamanger for db_manager object!
            await init_db_tables() #this will stay out regardless in case of server cold restart
                    
            key_res: dict = system_key(redis_url=redis_url, chroma_db_dir=chroma_db_dir, cohere_api_key=cohere_api_key, portable_cache_registry_db=Path(db_path))
            if not key_res["success"]:
                raise ValueError("Failed to initialize system keys. Verify if the keys are correct.")
            
            
            # This bit is for 0.1.9 (at import time embedding model will be loaded anyways so run this anytime u want) 
            """
            is_in_venv = sys.prefix != sys.base_prefix
            celery_name = "celery.exe" if os.name == "nt" else "celery"
            celery_executable = Path(sys.executable).parent / celery_name
            
            if is_in_venv and celery_executable.exists():
                celery_cmd = [
                    str(celery_executable),
                    "-A",
                    "TriCacheLLM_MMA.portable_cache_bgWorkers.portable_cache_celery_conf.celery_app",
                    "worker",
                    "--loglevel=info",
                    "-Q",
                    "ai"
                ]
                
                # Check OS and launch accordingly
                if os.name == "nt":
                    subprocess.Popen(celery_cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
                    print("Don't worry, TriCacheLLM_MMA is running celery background workers automatically")
                    
                elif sys.platform == "darwin":
                    try:
                        cmd_str = " ".join(celery_cmd)
                        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{cmd_str}"'])
                        print("Don't worry, TriCacheLLM_MMA is running celery background workers automatically")
                    except Exception:
                        subprocess.Popen(celery_cmd)    
                        print("Don't worry, TriCacheLLM_MMA is running celery background workers automatically")
                else:
                    # Linux / Unix fallback (runs as a background child process)
                    subprocess.Popen(celery_cmd)
                    print("Don't worry, TriCacheLLM_MMA is running celery background workers automatically")
            else:
                print("---WARNING--- auto celery start failed manually run this command:\ncelery -A TriCacheLLM_MMA.portable_cache_bgWorkers.portable_cache_celery_conf.celery_app worker --loglevel=info -Q ai")
            
        """
        
            return #user_id=0 doesnt need its own vdb! if you want to be it user-self be user_id=1 -> single user
                    #if you want multi-tanent then keep sending in user_ids lol
        
        async with db_manager.async_session() as db:
            ans: None | dict = await create_cache_vdb_inishiator(user_id=user_id, db=db) 
            
            if ans is None:
                print(f"CACHE_SYSTEM_INIT_SKIPPED: Already exists or in progress for user_id: {user_id}")
                return {
                    "success": False,
                    "task_id": None,
                    "status_msg": "already created or is being created"
                }
            
            if ans.get("success"):
                task_id = ans.get("task_id")
                print(f"CACHE_SYSTEM_INIT_STARTED_ASYNC with task_id: {task_id} for user_id: {user_id}")
                return {
                    "success": True,
                    "task_id": task_id,
                    "status_msg": "Started making"
                }
            else:
                print(f"CACHE_SYSTEM_INIT_FAILED for user_id: {user_id}")
                return {
                    "success": False,
                    "task_id": None,
                    "status_msg": "Initialization failed upstream"
                }
            
    except Exception as exc:
        print(f"CACHE_SYSTEM_INIT_ERROR where error: {str(exc)} and user_id: {user_id}")
        return {
            "success": False,
            "task_id": None,
            "status_msg": f"Error: {str(exc)}"
        }

async def check_cache(user_input: str, scope: Optional[str] = None, user_id: Any = 0) -> dict | None:
    print(f"CHECKING_MULTI_TIER_CACHE where user_id: {user_id}")
    try:
        async with db_manager.async_session() as db:
            path_record = await db.get(Paths, 1) #get me 1st row as this is consumer only thus singletn row, btw consumer can have n users dw!
            if not path_record or not path_record.redis_url:
                raise ValueError("System registry not initialized! Run create_cache_system first.")
            
            redis_url = path_record.redis_url
            cache_key: str = generate_cache_key(question=user_input, user_id=user_id)
            
            normal_redis = await get_redis(redis_url=redis_url) #dw this will work as when we import portable_cache_redis.py all loads
            cache_res: dict = await multi_tier_cache_system(
                scope=scope,
                cache_key=cache_key,
                normal_redis_instance=normal_redis,
                question=user_input,
                db=db,
                user_id=user_id
            )

            if cache_res and cache_res.get("success"):
                return cache_res.get("data")
            
            return None
    except Exception as e:
        print(f"Error checking cache for user_id {user_id}: {e}")
        return None

async def populate_cache(to_cache_answer, to_cache_question, user_id: Any = 0) -> dict | None:
    print(f"BACKGROUND_CACHE_POPULATION_FIRED where user_id: {user_id}")
    try:
        async with db_manager.async_session() as db:
            cache_vdb: Chroma | None = await get_user_cache_vdb(user_id=user_id, db=db)
            if cache_vdb is not None:
                ans: dict = await push_responce_in_cache_inishiator(model_output=to_cache_answer, user_id=user_id, question=to_cache_question)
                if ans["success"]:
                    return ans
            return None
    except Exception as e:
        print(f"Error populating cache for user_id {user_id}: {e}")
        return None

async def close_cache_system():
    print("SHUTTING_DOWN_PORTABLE_CACHE: Cleaning up resources...")

    from .portable_cache_schemas.portable_cache_dbConf import db_manager

    if db_manager.norma_engine is not None:
        try:
            await db_manager.norma_engine.dispose()
            print("Norma database engine disposed successfully.")
        except Exception as e:
            print(f"Error disposing norma_engine: {e}")

    if db_manager.celery_engine is not None:
        try:
            await db_manager.celery_engine.dispose()
            print("Celery database engine disposed successfully.")
        except Exception as e:
            print(f"Error disposing celery_engine: {e}")

    try:
        await redis_manager.close_redis_pool()
        print("Redis connection pool disconnected successfully.")
    except Exception as e:
        print(f"Error disconnecting Redis pool: {e}")

    print("Portable Cache shutdown complete!")

def check_tenant_creation_status(task_id, user_id) -> dict:
    task_id: str = str(task_id)
    user_id: int = int(user_id)
    
    from celery.result import AsyncResult
    from .portable_cache_bgWorkers.portable_cache_celery_conf import celery_app
    from celery.states import SUCCESS, FAILURE, RETRY
    
    async_result = AsyncResult(task_id, app=celery_app)
    state = async_result.state
    
    if state == SUCCESS:
        data={
            "status": "completed",
            "task_id": task_id,
            "state": state,
            "failed": False
    }
        return data
    
    elif state == FAILURE:
        data={
            "status": "failed",
            "task_id": task_id,
            "state": state,
            "failed": True
    }
        return data
    
    elif state == RETRY:    
        data={
            "status": "retrying",
            "task_id": task_id,
            "state": state,
            "failed": False
    }
        return data
    
    else:
        data={
            "status": "processing",
            "task_id": task_id,
            "state": state,
            "failed": False
    }
        return data
    
