from fastapi import FastAPI, Depends
# Import your actual package name/functions
from portable_cache_main import check_cache, populate_cache, create_cache_system, close_cache_system

app = FastAPI()

# 1. Initialization Route
"""For this first run aka user_id=0:"""
# That is for YOU, not your tenants! Make sure you run this first in your event loop.
# It creates an internal-use SQLite registry database for you and your tenants (this DB is totally unrelated to your main application DB).
# It also creates a vector database internally whose state is tracked by that internal registry.
# It also connects to background workers. Before running your app, make sure to spin up your Celery worker in a separate terminal:
    # celery -A portable_cache_bgWorkers.portable_cache_celery_conf.celery_app worker --loglevel=info -Q ai
    # This worker will handle async vector database creation and background cache population!
    
# Visit: https://docs.cohere.com/reference/check-api-key -> to get your Cohere API key. 
# (Note: I made a custom ranker, but didn't ship it in the core library since it requires local execution...)
# Once you see a successful result from create_cache_system() separately, you can freely use and abuse create_cache_system, check_cache and populate_cache as you see fit!
# create_cache_system is a one-time setup run for your machine presumably for user_id=0. After that, everything needed to run cache checks and populations will be fully available.

#After First Run:
# POINT TO NOTE IS: for user_id=0 (YOU) it'll set all things up, but when you need it for multiple users then call it for each user's user_id
# Then for each user you'll have a saprate cache_vdb mininal implemntation:
"""
@app.post("/xyz")
async def ask_question(user_payload: QuestionRequest, db: AsyncSession = Depends(get_db), user_jwt_payload: TokenDataSchema = Depends(get_user_jwt_payload)):
    user_id = user_jwt_payload.user_id
    question = user_jwt_payload.user_question
    
    result = await create_cache_system(
        user_id=user_id #-> this will create everything needed for that user's cache 
    )
    
    #as all fields expect user_id in create_cache_system after first run are optional 
"""

@app.post("/api/tenant/{user_id}/init")
async def initialize_tenant(user_id: int):
    result = await create_cache_system(
        redis_url="redis://localhost:6379/0",
        cohere_api_key="YOUR_COHERE_API_KEY",
        chroma_db_dir="./chroma_db",
        db_path="./cache.db",
        user_id=user_id,
    )
    return {"status": "success", "details": result}


# 2. Main Chat / AI Query Route (Multi-Tenant Caching Flow)
"""
The vector metadata looks like this:
cache_metadata = {
    "user_id": user_id,
    "question": question,
    "llm_response": response_json_str,
    "created_at": datetime.now(timezone.utc).isoformat()
}
This metadata is wrapped inside a LangChainDocument:

cache_document = LangChainDocument(
    page_content=question,
    metadata=cache_metadata
)

a) check_cache handles 3 tiers of caching as the name suggests:
    - Tier 1: Exact Match (Redis)
    - Tier 2: Vector Match (Redis Search)
    - Tier 3: Deep Vector Search & Reranking (Chroma VDB + Cohere)

b) Internally, multi_tier_cache_system() runs where Tier 3 is responsible for automatically populating Redis Search and exact-match Redis caches.

c) If you want to perform search and filtering by metadata (or more advanced filtering like date ranges), feel free to modify it here:
    chroma_result: dict = await get_cached(question=question, cache_vdb=cache_vdb, user_id=user_id)
    
    And initialize it here:
    fetched_and_scores: list[tuple[LangChainDocument, float]] = await asyncio.to_thread(
        cache_vdb.similarity_search_with_score,
        query=question,
        k=10,
        filter={"timestamp": {"$gte": seven_days_ago}}  # <-- Chroma metadata filter 
    ) 
    
d) Similarly, if you want to add more custom metadata fields, you can do that in:
    async def push_response_in_cache_async(task_instance: Any, user_id, question: str, model_output_dict: dict) -> str | None:
    
    And add them here:
    cache_metadata = {
        "user_id": user_id,
        "question": question,
        "llm_response": response_json_str,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "timestamp": time.time(),
        # ADD NEW FIELDS HERE -> e.g., I've used dict[document_id, latest_version] filtering for myself in another project!
    }
"""

@app.post("/api/chat")
async def chat_endpoint(user_id: int, question: str):
    
    # Check the 3-tier cache first (Tier 1 Redis -> Tier 2 Vector -> Tier 3 Chroma + Cohere)
    cached_response = await check_cache(
        user_id=user_id,
        question=question
    )
    
    if cached_response:
        # Cache Hit! Instant return, bypassing expensive LLM inference
        return {"source": "cache", "answer": cached_response}

    # Cache Miss: Fall back to your upstream AI model / LLM pipeline
    llm_answer = await your_ai(question)

    # Populate the cache in the background (via Celery) for future queries
    await populate_cache(
        user_id=user_id,
        query=question,
        response=llm_answer
    )
    
    return {"source": "llm_generation", "answer": llm_answer}

async def your_ai(question: str) -> str:
    # Simulate your heavy upstream LLM call here
    return f"This is the generated AI response for: {question}"