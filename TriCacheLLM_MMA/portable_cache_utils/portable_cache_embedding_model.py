# # File: portable_cache_utils/portable_cache_embedding_model.py
from langchain_huggingface import HuggingFaceEmbeddings
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings

embedding_model = HuggingFaceEmbeddings(
    model_name=get_settings().portable_cache_cache_proj_embedding_model,
    cache_folder=get_settings().portable_cache_chroma_db_dir
)