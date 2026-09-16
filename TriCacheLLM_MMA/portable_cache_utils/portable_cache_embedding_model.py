# File: portable_cache_utils/portable_cache_embedding_model.py

#on each --reload this reads weights
from langchain_huggingface import HuggingFaceEmbeddings
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings

embedding_model = HuggingFaceEmbeddings(
    model_name=get_settings().portable_cache_cache_proj_embedding_model,
    cache_folder=get_settings().portable_cache_chroma_db_dir
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