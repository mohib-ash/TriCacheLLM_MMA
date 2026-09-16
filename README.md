# TriCacheLLM_MMA

## Portable 3-Tier Semantic Cache for LLM Applications

**TriCacheLLM_MMA** is a portable asynchronous caching system designed for Ai based ***web applications*** that repeatedly ask LLMs similar or identical questions.

It combines:

* **Exact Redis caching**
* **Semantic Redis HNSW search**
* **Persistent vector-database caching**
* **Cohere reranking**
* **Celery background workers**
* **Per-user / per-tenant cache isolation**
* **SQLite-backed cache infrastructure state**
* **Automatic cache promotion between tiers**

The goal is simple:

> Avoid paying the cost of expensive LLM inference when an equivalent or sufficiently similar answer has already been generated.

The package is designed so that the consuming application only needs to initialize the cache infrastructure and use three simple operations:

```python
create_cache_system(...)
check_cache(...)
populate_cache(...)
check_tenant_creation_status(...)
```

The internal infrastructure handles the multi-tier lookup and promotion logic.

--- 

> **💡 Note for PyPI Visitors:** If you are reading this on PyPI, please check out the [TriCacheLLM_MMA GitHub Repository](https://github.com/mohib-ash/TriCacheLLM_MMA) for the most up-to-date documentation, integration guides, and advanced examples!


---

# Architecture

TriCacheLLM_MMA uses three cache tiers.

```text
                         USER QUESTION
                               │
                               ▼
                    ┌─────────────────────┐
                    │        TIER 1       │
                    │    Exact Redis KV   │
                    │                     │
                    │ Fastest lookup      │
                    │ Exact question      │
                    └──────────┬──────────┘
                               │ MISS
                               ▼
                    ┌─────────────────────┐
                    │        TIER 2       │
                    │   Redis HNSW Vector │
                    │       Search        │
                    │                     │
                    │ Semantic similarity │
                    └──────────┬──────────┘
                               │ MISS
                               ▼
                    ┌─────────────────────┐
                    │        TIER 3       │
                    │ Persistent VDB      │
                    │     + Cohere        │
                    │      Reranking      │
                    └──────────┬──────────┘
                               │
                               ▼
                    Persistent Vector Store
```

## Tier 1: Exact Redis

The first lookup is an exact question lookup.

This is the fastest path.

If the same question was previously cached, the system can immediately return the stored response without embedding generation, vector search, VDB access, or LLM inference.

```text
Question
   ↓
Exact Redis
   ↓
HIT
   ↓
Cached Answer
```

---

## Tier 2: Semantic Redis HNSW

If Tier 1 misses, the question is embedded and searched against a Redis HNSW vector index.

This allows small variations in wording to hit the cache.

For example:

```text
Cached:
How can I run Celery tasks asynchronously using Redis?

New:
How can I run Celery tasks asynchronously using Redis ?
```

The second question is not an exact string match, but it can still be recognized as semantically equivalent.

```text
T1 MISS
   ↓
T2 semantic search
   ↓
HIT
   ↓
Cached Answer
```

Tier 2 is intentionally optimized for fast semantic cache retrieval.

---

# Tier 3: Persistent VDB-Backed Cache

Tier 3 is the persistent cache layer.

The persistent vector database acts as the long-term backing store for cached Q&A entries.

When Tier 1 and Tier 2 miss, Tier 3 performs the persistent lookup.

The persistent lookup can use:

* vector similarity
* metadata
* provenance information
* additional filtering
* Cohere reranking

The exact persistent retrieval logic lives inside the VDB cache implementation.

A Tier 3 hit is not simply returned and forgotten.

The response is promoted upward:

```text
Tier 3 / VDB HIT
       │
       ▼
Populate Tier 2
       │
       ▼
Populate Tier 1
       │
       ▼
Return cached response
```

This means frequently accessed answers naturally migrate toward the faster cache tiers.

---

# Cache Promotion

One of the central design principles of TriCacheLLM_MMA is **upward cache promotion**.

```text
                    ┌──────────────┐
                    │    TIER 1    │
                    │ Exact Redis  │
                    └──────▲───────┘
                           │
                           │ promotion
                           │
                    ┌──────┴───────┐
                    │    TIER 2    │
                    │ Redis HNSW   │
                    └──────▲───────┘
                           │
                           │ promotion
                           │
                    ┌──────┴───────┐
                    │    TIER 3    │
                    │ Persistent   │
                    │ VDB-backed   │
                    └──────────────┘
```

Examples:

### Exact hit

```text
T1 HIT
↓
Return immediately
```

### Semantic hit

```text
T1 MISS
↓
T2 HIT
↓
Return response
↓
Promote / fill T1
```

### Persistent hit

```text
T1 MISS
↓
T2 MISS
↓
T3 HIT
↓
Promote to T2
↓
Promote to T1
↓
Return response
```

### Complete miss

```text
T1 MISS
↓
T2 MISS
↓
T3 MISS
↓
Your LLM / AI pipeline
↓
populate_cache()
↓
Persistent cache seeded
```

---

# Important V1 Semantics

`populate_cache()` and `check_cache()` have intentionally different responsibilities.



`populate_cache()` seeds the persistent cache VDB.

It does **not** directly populate every cache tier.

```text
populate_cache()
       ↓
Persistent VDB
```

The next `check_cache()` can discover that entry through Tier 3 and promote it upward.

This separation keeps the cache population path simple while allowing `check_cache()` to control cache promotion.

---

# Multi-Tenant Architecture

The cache system supports multiple users / tenants.

Each consumer user receives an isolated persistent cache VDB.

Conceptually:

```text
Consumer Application
        │
        ├── User 1
        │     └── Cache VDB 1
        │
        ├── User 2
        │     └── Cache VDB 2
        │
        └── User N
              └── Cache VDB N
```

The package itself does not own the consumer application's user table.

Instead, the consumer application provides the users.

The cache package maintains its own cache infrastructure state.

```text
Consumer Database
│
├── users
│
├── paths
│
└── cache_vdb_resources
```

`cache_vdb_resources.user_id` references the consumer application's:

```text
users.user_id
```

Therefore, the consumer application must provide a compatible `users` table with a unique `user_id` primary key.
If you don't want your web app to have Multiple Tenants then asside form user_id=0, keep providing same user_id.
    For a complete integration example, see:
`details_and_examples.py`.

---

# Internal Database

TriCacheLLM_MMA uses SQLite for its internal cache infrastructure state.

The package tracks information such as:

* configured Redis location
* Chroma storage location
* registry database path
* Cohere configuration
* per-user VDB status
* VDB version
* VDB path
* VDB creation failures

The runtime state is represented by tables including:

```text
paths
cache_vdb_resources
```

The consumer application's own database remains separate from the cache system's infrastructure state.

---

# Requirements

## Python

Recommended:

```text
Python 3.11+
```

## Infrastructure

TriCacheLLM_MMA currently expects:

* Redis
* Celery
* SQLite
* ChromaDB
* Cohere API access

Your application can use any framework.

FastAPI is used in the included example because it provides a convenient demonstration.

---

# Installation

Install from PyPI:

```bash
pip install TriCacheLLM_MMA
```

Or install the development version directly from the repository:

```bash
pip install .
```

PyPI link: https://pypi.org/project/TriCacheLLM-MMA/0.1.3/

---

# Redis

Start a Redis server before using the cache.

The default example uses:

```text
redis://localhost:6379/0
```

You can provide your Redis URL through:

```python
redis_url="your-redis-url"
```

---

# Celery Worker

TriCacheLLM_MMA uses Celery for background operations such as:

* persistent cache VDB creation
* persistent cache population

After installing the package, start the cache worker in a **separate terminal**:

```bash
celery -A portable_cache_bgWorkers.portable_cache_celery_conf.celery_app worker --loglevel=info -Q ai
```

You do **not** need to navigate into the package's `site-packages` directory.

The command imports the installed package through the active Python environment.

A typical setup therefore looks like:

```text
Terminal 1
└── Your application
    └── FastAPI / Flask / Django / custom service

Terminal 2
└── TriCacheLLM_MMA Celery worker
```

Keep the Celery worker running while using the cache.

---

# Cohere

The persistent cache tier uses Cohere reranking.

Create a Cohere API key and provide it during initialization.

```python
cohere_api_key="YOUR_COHERE_API_KEY"
```

Do not commit your API key to Git.

For production deployments, use environment variables or your application's secret-management system.

---

# Quick Start

A minimal integration looks like this:

```python
from fastapi import FastAPI, Depends
from TriCacheLLM_MMA import (
    create_cache_system,
    populate_cache,
    check_cache,
    close_cache_system,
    check_tenant_creation_status,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_cache_system()


app = FastAPI(
    title="TriCacheLLM_MMA V1 Route Test",
    lifespan=lifespan,
)


# 1. ADMIN SETUP (Run once on startup / first deploy with user_id=0)
@app.post("/api/consumer/init")
async def initialize_consumer():
    user_id: int = 0
    result = await create_cache_system(
        redis_url="redis://localhost:6379/0",
        cohere_api_key="KEY_HERE",
        chroma_db_dir="./chroma_db",
        db_path="./cache.db",
        user_id=user_id, 
    )
    return {"status": "success", "details": result}


# 2. TENANT ENDPOINT (Zero boilerplate for subsequent users)
@app.post("/xyz")
async def ask_question(
    user_payload: QuestionRequest, 
    user_jwt_payload: TokenDataSchema = Depends(get_user_jwt_payload)
):
    user_id = user_jwt_payload.user_id
    question = user_payload.user_question  # or user_payload.question
    
    # Lazy-initialize tenant's isolated cache partition (no keys needed!)
    await create_cache_system(user_id=user_id)
    
    # Check cache before hitting your expensive AI pipeline
    if cached := await check_cache(user_id=user_id, user_input=question):
        return {"source": "cache", "response": cached}

    # Run your heavy AI / RAG pipeline
    llm_response = await your_ai_pipeline(question)

    # Populate the persistent cache asynchronously
    await populate_cache(
        user_id=user_id,
        to_cache_question=question,
        to_cache_answer=llm_response,
    )

    return {"source": "ai", "response": llm_response}

```
The core cache operations are asynchronous Python APIs and are not inherently tied to FastAPI.
The included integration example uses FastAPI because it provides a convenient demonstration of multi-user request handling.

V1 is primarily demonstrated in a web-service architecture, while future versions aim to make standalone application integration equally straightforward.
***NOTE: When you import files they'll send request to hugging face to get embedding model weights but only once! untill you reload server***

---





# Cache Data Model

Cached entries conceptually contain information similar to:

```python
cache_metadata = {
    "user_id": user_id,
    "question": question,
    "llm_response": response_json_str,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "timestamp": time.time(),
}
```

The question is stored as the vector document content, while the answer and supporting information are stored as metadata.

This allows the persistent VDB to perform semantic retrieval while retaining the original response.

---

# Extending Metadata Filtering

The persistent VDB layer can be customized if your application requires additional constraints.

For example, you may want to restrict cache retrieval to:

* a specific time period
* a specific document version
* a specific tenant resource
* a specific source
* a specific application state

The persistent retrieval logic can be extended around the VDB lookup.




This allows applications to combine semantic retrieval with deterministic metadata constraints.

---

# Adding Custom Metadata

Additional metadata can be added to the cache payload.

The cache population path constructs metadata similar to:



For example:

```python
cache_metadata = {
    "user_id": user_id,
    "question": question,
    "llm_response": response_json_str,
    "created_at": datetime.now(timezone.utc).isoformat(),
    "timestamp": time.time(),
    "document_id": document_id,
    "latest_version": latest_version,
}
```

Applications can then use those fields as part of their cache validity and retrieval constraints.

---

# Embedding Model

The default embedding model is:

```text
sentence-transformers/all-MiniLM-L6-v2
```

The embedding model is loaded once per Python process and reused by the cache operations handled by that process.

The same model instance is shared across users handled by that process.

Multiple independent worker processes may naturally maintain their own model instance.

This avoids repeatedly loading the embedding model for every user or request.

---

# Persistence

The package separates code from runtime cache state.

After installation, the Python package lives inside the consumer's Python environment.

Runtime state can live outside the installed package:

```text
your_project/
│
├── .portable_cache_internal/
│   └── .env_protable_cache
│
├── cache.db
│
├── chroma_db/
│
├── your_app/
│
└── .venv/
    └── site-packages/
        └── TriCacheLLM_MMA/
```

The cache registry records the important absolute runtime paths so the cache infrastructure does not have to depend on where the package itself was installed.

---

# Runtime Configuration

The package creates an internal configuration area:

```text
.portable_cache_internal/
```

The configuration file contains values such as:

```text
PORTABLE_CACHE_REDIS_URL
PORTABLE_CACHE_COHERE_API_KEY
PORTABLE_CACHE_CHROMA_DB_DIR
PORTABLE_CACHE_REGISTRY_DB
```

Do not commit this directory.

It is included in the repository's `.gitignore`.

---

# Security

## Never commit API keys

Do not place real API keys into:

```text
example_user_experice.py
```

Use:

```python
cohere_api_key="YOUR_COHERE_API_KEY"
```

or load secrets through your application's environment/secret-management system.

## Runtime state

The following should remain local to the consumer environment:

```text
.portable_cache_internal/
*.db
chroma_db/
```

These are runtime artifacts, not source code.

---

# Project Structure

The V1 repository is intentionally lightweight.

```text
TriCacheLLM_MMA/
│
├── portable_cache_main.py
├── portable_cache_redis.py
├── portable_cache_dbSchema.py
│
├── portable_cache_Ai/
│   └── portable_cache_rerankAi.py
│
├── portable_cache_bgWorkers/
│   ├── portable_cache_celery_conf.py
│   └── portable_cache_workers.py
│
├── portable_cache_schemas/
│   ├── portable_cache_dbBase.py
│   ├── portable_cache_dbConf.py
│   └── portable_cache_schemas.py
│
├── portable_cache_utils/
│   ├── portable_cache_embedding_model.py
│   └── protable_cache_DynamicEnv_maker.py
│
├── example_user_experice.py
├── pyproject.toml
├── req.txt
└── README.md
```

---

# Design Philosophy

TriCacheLLM_MMA follows a simple principle:

> **Cheap exact lookup first. Cheap semantic lookup second. Expensive persistent retrieval last. LLM inference only after the cache has genuinely missed.**

This creates a natural latency hierarchy:

```text
T1
│
├── Exact
├── Lowest overhead
└── Fastest

T2
│
├── Semantic
├── Embedding + HNSW
└── Still lightweight

T3
│
├── Persistent
├── Vector search
├── Metadata constraints
└── Reranking

LLM
│
└── Expensive generation
```

The cache therefore attempts to stop a request as early as possible.

---

# Why Three Tiers?

A single semantic vector database is powerful, but using it for every request introduces unnecessary work.

For repeated exact questions, performing:

```text
embedding
→ vector search
→ reranking
```

is unnecessary.

Similarly, using only exact Redis cannot handle:

```text
"What is Redis used for?"

vs.

"Can you explain what Redis is used for?"
```

A multi-tier architecture allows each retrieval mechanism to handle the workload it is best suited for.

---

# V1 Scope

This release intentionally focuses on the core cache architecture.

Included:

* Exact Redis cache
* Redis HNSW semantic cache
* Persistent Chroma cache
* Cohere reranking
* Celery background processing
* Multi-user cache isolation
* SQLite infrastructure registry
* Cache promotion
* Portable initialization
* Async API

Not included as first-class abstractions:

* automatic Celery process management
* distributed task orchestration beyond Celery
* cloud-specific deployment
* automatic secret management
* advanced configuration framework
* full SDK-style class abstraction
* production observability platform
* automatic infrastructure provisioning

These can be considered for future versions.

---



# Consumer Responsibility

The consuming application is responsible for:

* running Redis
* running the Celery worker
* providing Cohere credentials
* maintaining its own users
* executing the actual LLM / AI pipeline
* deciding when a question should be cached
* deciding what constitutes an acceptable cache hit for its application

TriCacheLLM_MMA is responsible for:

* cache infrastructure
* multi-tier retrieval
* semantic cache search
* persistent cache storage
* cache promotion
* background cache operations
* cache VDB lifecycle state

This separation allows the package to remain independent of any specific LLM provider or application framework.

---

# Roadmap

## V2: Runtime and Developer Experience

Potential V2 improvements may include:

* pluggable background execution backends, including Celery, asyncio and synchronous execution
* remove the requirement for consumers to manually start a Celery worker when using simpler backends
* configurable cache thresholds and TTLs
* pluggable rerankers and embedding providers
* richer metadata filtering
* improved observability
* stronger automated test coverage
* cleaner class-based SDK API
* packaging and deployment improvements

## V3: Standalone and Broader Application Support

Potential V3 improvements may include:

* remove the current FastAPI-oriented integration assumptions
* support standalone Python applications and services
* support broader application architectures beyond web-based APIs
* provide more flexible integration patterns for single-user and multi-user applications
* simplified deployment for standalone environments

The current V1 intentionally keeps the architecture close to the underlying implementation rather than hiding every component behind abstractions.

---


# License

This project is licensed under the **GNU Lesser General Public License v3.0 (LGPLv3)**. 
See the [LICENSE](LICENSE) file for details.

---

# Author

**TriCacheLLM_MMA built by Mohib Ashfaq**

A portable three-tier semantic caching system for LLM applications.

Built around:

```text
Redis
+
Redis HNSW
+
Chroma
+
Cohere
+
Celery
```

with the goal of making expensive LLM inference the **last resort rather than the default path**.

---

# Disclaimer: 
This software is provided "as is" without warranty of any kind. The author is not responsible for any data loss, system failures, or damages arising from its use.