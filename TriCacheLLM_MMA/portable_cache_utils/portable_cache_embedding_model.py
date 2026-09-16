# File: portable_cache_utils/portable_cache_embedding_model.py

import os
import sys
from pathlib import Path
from langchain_huggingface import HuggingFaceEmbeddings
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings


def get_embedding_cache_dir() -> Path:
    """
    Return a stable per-user cache directory for the Hugging Face
    embedding model, independent of the consumer application's location,
    respecting OS conventions and XDG standards.
    """
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "TriCacheLLM_MMA" / "embedding_model"
        return Path.home() / "AppData" / "Local" / "TriCacheLLM_MMA" / "embedding_model"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "TriCacheLLM_MMA" / "embedding_model"

    # Linux / Unix: Respect XDG_CACHE_HOME if explicitly configured
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "TriCacheLLM_MMA" / "embedding_model"
    
    return Path.home() / ".cache" / "TriCacheLLM_MMA" / "embedding_model"


# Ensure the stable cache path exists prior to initialization
embedding_cache_dir = get_embedding_cache_dir()
embedding_cache_dir.mkdir(parents=True, exist_ok=True)


#on each --reload this reads weights
embedding_model = HuggingFaceEmbeddings(
    model_name=get_settings().portable_cache_cache_proj_embedding_model,
    cache_folder=str(embedding_cache_dir),
)



#lazy load, on --reload it will read weights but not at the start but when needed (un-comment me if u need lazyload)
"""
from langchain_huggingface import HuggingFaceEmbeddings
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings

class EmbeddingModel:
    def __init__(self):
        self.model = None

    def get_model(self):
        if self.model is None:
            self.model = HuggingFaceEmbeddings(
                model_name=get_settings().portable_cache_cache_proj_embedding_model,
                cache_folder=get_settings().portable_cache_chroma_db_dir,
            )
        return self.model
embedding_manager = EmbeddingModel()
"""