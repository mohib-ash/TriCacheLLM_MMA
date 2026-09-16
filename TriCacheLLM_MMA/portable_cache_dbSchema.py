# File: portable_cache_dbSchema.py
from sqlalchemy import Column, Integer, DateTime, Boolean, String, Text, ForeignKey, Enum as SQLEnum, text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from .portable_cache_schemas.portable_cache_dbBase import Base
from .portable_cache_schemas.portable_cache_schemas import CacheVDBStatus

class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_banned = Column(Boolean, nullable=False, server_default=text("0"))

    cache_vdb_resource = relationship("CacheVDBResource", back_populates="user", uselist=False, cascade="all, delete-orphan")


class CacheVDBResource(Base):
    __tablename__ = "cache_vdb_resources"

    cache_vdb_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    status = Column(SQLEnum(CacheVDBStatus), default=CacheVDBStatus.PENDING, nullable=False, index=True)
    version = Column(Integer, default=0, server_default="0", nullable=False)

    vdb_path = Column(String(512), nullable=True)
    failure_reason = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="cache_vdb_resource")


class Paths(Base):
    __tablename__ = "paths"
    
    id = Column(Integer, primary_key=True, default=1)
    portable_env_path = Column(String(512), nullable=False)
    chroma_db_dir = Column(String(512), nullable=False)
    redis_url = Column(String(512), nullable=False)
    db_path = Column(String(512), nullable=False)
    cohere_api_key = Column(String(512), nullable=False)