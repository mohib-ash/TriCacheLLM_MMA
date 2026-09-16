# File: portable_cache_Ai/portable_cache_rerankAi.py
import cohere
from typing import Optional, Any
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from langchain_core.documents import Document as LangChainDocument
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings
from ..portable_cache_dbSchema import Paths

class CohereManager:
    """Encapsulates the Cohere client to prevent import-time crashes."""
    def __init__(self):
        self._client: Optional[cohere.ClientV2] = None
        self._current_api_key: Optional[str] = None

    def _get_cohere_api_key_from_registry(self) -> Optional[str]:
        """Sneaks a peek into the SQLite registry DB to grab the true source-of-truth cohere_api_key."""
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
            if path_record and path_record.cohere_api_key:
                return path_record.cohere_api_key
        except Exception:
            pass
        return None

    def get_client(self, api_key: Optional[str] = None) -> cohere.ClientV2:
        """Lazily initializes the Cohere V2 client using explicit arg, registry DB, or settings/env."""
        settings = get_settings()
        
        target_api_key = api_key or self._get_cohere_api_key_from_registry() or settings.portable_cache_cohere_api_key
        
        if not target_api_key:
            raise ValueError("Cohere API key not found! Run create_cache_system first or set your API key.")
        
        if self._client is None or self._current_api_key != target_api_key:
            self._current_api_key = target_api_key
            self._client = cohere.ClientV2(api_key=target_api_key)
        
        return self._client

cohere_manager = CohereManager()

async def portable_cache_cohere_rerank(
    question: str, 
    user_id: Any,
    received_docs: list[LangChainDocument], 
    top_k: int,
) -> dict:
    """
    Enterprise portable wrapper for document reranking using Cohere's native cross-encoder API.
    Returns a clean dict: {"success": bool, "data": list[LangChainDocument] | None, "error": str | None}
    """
    print(f"CACHE_RERANK_STARTED where user_id: {user_id}") 
    
    if not question or not question.strip():
        print(f"CACHE_RERANK_FAILED: Empty input question for user_id: {user_id}")
        return {
            "success": False,
            "data": None,
            "error": "Input text is empty"
        }
    
    if not received_docs:
        print(f"CACHE_RERANK_FAILED: No documents received for user_id: {user_id}")
        return {
            "success": False,
            "data": None,
            "error": "Re-ranker got no data, meaning retriever returned empty candidates."
        }

    documents_text = [doc.page_content for doc in received_docs]
    
    try:
        print(f"CACHE_RERANK_PROVIDER_REQUEST where user_id: {user_id}")
        client = cohere_manager.get_client()
        rerank_model = get_settings().portable_cache_cohere_rerank_model
        
        response = client.rerank(
            model=rerank_model,
            query=question,
            documents=documents_text,
            top_n=min(top_k, len(documents_text)),
        )
        print(f"CACHE_RERANK_PROVIDER_SUCCESS where user_id: {user_id}")
    except Exception as e:
        print(f"CACHE_RERANK_ERROR where error: {str(e)} and user_id: {user_id}")
        return {
            "success": False,
            "data": None,
            "error": str(e)
        }

    print(f"CACHE_RERANK_MAPPING_STARTED where user_id: {user_id}")
    reranked_docs: list[LangChainDocument] = []
    try:
        for result in response.results:
            original_doc = received_docs[result.index]
            updated_metadata = dict(original_doc.metadata or {})
            updated_metadata["rerank_score"] = float(result.relevance_score)

            reranked_docs.append(
                LangChainDocument(
                    page_content=original_doc.page_content,
                    metadata=updated_metadata,
                )
            )
        print(f"CACHE_RERANK_MAPPING_SUCCESS where user_id: {user_id}")
    except Exception as e:
        print(f"CACHE_RERANK_MAPPING_ERROR where error: {str(e)} and user_id: {user_id}")
        return {
            "success": False,
            "data": None,
            "error": "Failed to map reranked results to documents."
        }

    print(f"CACHE_RERANK_COMPLETED where user_id: {user_id}")
    return {
        "success": True,
        "data": reranked_docs,
        "error": None
    }