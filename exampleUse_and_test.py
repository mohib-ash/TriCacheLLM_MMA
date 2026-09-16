import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from TriCacheLLM_MMA.portable_cache_main import (
    create_cache_system,
    populate_cache,
    check_cache,
    close_cache_system,
    check_tenant_creation_status
)
"""
But for you it would be like:
from TriCacheLLM_MMA import (
    create_cache_system,
    populate_cache,
    check_cache,
    close_cache_system,
    check_tenant_creation_status,
)
"""



# Shut-down 
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_cache_system()


app = FastAPI(
    title="TriCacheLLM_MMA V1 Route Test",
    lifespan=lifespan,
)


#NOTE: model will download once at import time



# Request schemas
class PopulateRequest(BaseModel):
    user_id: int
    question: str
    answer: dict


class CacheCheckRequest(BaseModel):
    user_id: int
    question: str



# 1. SYSTEM INITIALIZATION
"""
i am sure i don't have to tell you this, but use your .env here instead of pasting Api keys and stuff directly into code
example:
class Settings(BaseSettings):
    cohere_api_key: str
    model_config = SettingsConfigDict(env_file=".env")
    
settings = Settings()
foo(
    cohere_api_key=settings.cohere_api_key
    )
"""
@app.post("/api/consumer/init")
async def initialize_consumer():
    user_id: int = 0
    result = await create_cache_system(
        redis_url="redis://localhost:6379/0",
        cohere_api_key="KEY_HERE",
        chroma_db_dir="./chroma_db", #<- This will be the dir name which will hold each chorma vector data base chroma_db/user_1/, user_2, ...
        db_path="./cache.db_MMA", #<- add _MMA porstfix for you ease as it will also act as file name
        user_id=user_id, #for this value expect None
    )
    return {"status": "success", "details": result}



# 2. TENANT INITIALIZATION
@app.post("/cache/tenant/{user_id}/init")
async def initialize_tenant(user_id: int):
    """
    Initialize the cache infrastructure for one tenant.

    After the first system initialization, the tenant call only
    needs the tenant's user_id because the package retrieves the
    stored infrastructure configuration.
    """

    if user_id == 0:
        raise HTTPException(
            status_code=400,
            detail="Use /api/consumer/init for user_id=0.",
        )

    result: dict[str, bool | str] | None = await create_cache_system(
        user_id=user_id,
    )

    if not result or not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Tenant initialization failed.",
                "user_id": user_id,
                "result": result,
            },
        )
        
    #u get task_id -> btw! u can use it in our polling function 
    task_id = result["task_id"]

    
    return {
        "stage": "tenant_initialization",
        "user_id": user_id,
        "details": result,
        "task_id": task_id
    }



# 2.5 Polling end-point:
@app.get("/cache/tenant/{user_id}/init/status/{task_id}")
async def tenant_initialization_status(
    user_id: int,
    task_id: str,
):
    return check_tenant_creation_status(task_id, user_id)





# 3. CACHE POPULATION
@app.post("/cache/populate")
async def populate_cache_route(request: PopulateRequest):
    """
    Queue one Q&A pair for cache population.

    This uses the actual public package argument names:
        to_cache_question
        to_cache_answer
        user_id => so cache is for that user
    """

    result: dict | None = await populate_cache(
        to_cache_question=request.question,
        to_cache_answer=request.answer,
        user_id=request.user_id,
    )

    if not result or not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Cache population was not accepted.",
                "user_id": request.user_id,
                "result": result,
            },
        )

    return {
        "status": "success",
        "stage": "cache_population",
        "user_id": request.user_id,
        "details": result,
    }



# 4. CACHE CHECK
@app.post("/cache/check")
async def check_cache_route(request: CacheCheckRequest):
    """
    Run the complete three-tier cache lookup.

    T1 -> Exact Redis
    T2 -> Semantic Redis HNSW
    T3 -> Persistent Chroma + Cohere
    """

    result = await check_cache(
        scope=None,
        user_input=request.question,
        user_id=request.user_id,
    )

    if result is None:
        return {
            "status": "cache_miss",
            "user_id": request.user_id,
            "result": None,
        }

    return {
        "status": "cache_hit",
        "user_id": request.user_id,
        "result": result,
    }






# CURL TEST SEQUENCE
#
# 1. SYSTEM INITIALIZATION
#
# curl -X POST http://127.0.0.1:8000/cache/system/init
#
#
# 2. TENANT INITIALIZATION
#
# curl -X POST http://127.0.0.1:8000/cache/tenant/1/init
#
#
# 3. POPULATE
#
# curl -X POST http://127.0.0.1:8000/cache/populate \
#   -H "Content-Type: application/json" \
#   -d '{
#     "user_id": 1,
#     "question": "What is the capital of France?",
#     "answer": {
#       "answer": "The capital of France is Paris.",
#       "source": "route-test"
#     }
#   }'
#
#
# 4. CHECK
#
# curl -X POST http://127.0.0.1:8000/cache/check \
#   -H "Content-Type: application/json" \
#   -d '{
#     "user_id": 1,
#     "question": "What is the capital of France?"
#   }'
#
#
# 5. CHECK AGAIN
#
# curl -X POST http://127.0.0.1:8000/cache/check \
#   -H "Content-Type: application/json" \
#   -d '{
#     "user_id": 1,
#     "question": "What is the capital of France?"
#   }'
#
