# File: portable_cache_schemas/portable_cache_dbConf.py
import sqlite3
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import event
from ..portable_cache_schemas.portable_cache_dbBase import Base
from ..portable_cache_utils.protable_cache_DynamicEnv_maker import get_settings


class DatabaseManager:
    """
    Professional Singleton Manager to handle dynamic engine creation 
    without module-level 'None' globals or manual boot order guessing.
    """
    def __init__(self):
        self.celery_engine = None
        self.norma_engine = None
        self.CelerySessionLocal = None
        self.AsyncSessionLocal = None
        self._initialized = False

    def initialize(self, db_path: str | Path):
        """Explicitly initializes the engines and session makers."""
        if self._initialized:
            return

        resolved_path = Path(db_path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        DATABASE_URL = f"sqlite+aiosqlite:///{resolved_path}"

        self.celery_engine = create_async_engine(
            DATABASE_URL,
            connect_args={"timeout": 30},
        )
        self.norma_engine = create_async_engine(
            DATABASE_URL,
            pool_size=20,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=3600
        )

        self.CelerySessionLocal = async_sessionmaker(
            bind=self.celery_engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )

        self.AsyncSessionLocal = async_sessionmaker(
            bind=self.norma_engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )

        @event.listens_for(self.celery_engine.sync_engine, "connect")
        #"connect" is a SQLAlchemy lifecycle event. It tells SQLAlchemy: "Fire this function the exact millisecond a brand new raw 
        #connection to the SQLite database is successfully opened."
        #now sqlite3 is syncro to rlly catch the new connection we need to tap into sync_engine!
        def set_sqlite_pragma(dbapi_connection, connection_record):
            #raw database connection -> dbapi_connection
            #connection_record -> its detail
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
            #no return was intentional

        self._initialized = True #made ture for said obj

    def _auto_bootstrap_if_needed(self):
        """Self-heals and auto-initializes if a worker or process calls it blindly."""
        if self._initialized:
            return #now we return not above here

        try:
            settings = get_settings()
            registry_db = getattr(settings, "portable_cache_registry_db", None)
            if registry_db and Path(registry_db).exists():
                conn = sqlite3.connect(registry_db)
                cursor = conn.cursor()
                cursor.execute("SELECT db_path FROM paths WHERE id = 1")
                row = cursor.fetchone()
                conn.close()
                
                if row and row[0]:
                    self.initialize(row[0])
                    return
        except Exception:
            pass

        raise RuntimeError(
            "Database not initialized! Call init_cache_database(db_path) explicitly "
            "or ensure the registry database is seeded."
        )

    #oh ok ig ill tell u, @property is like typedeff of c++ on drungs asside form being a rename its capable of running stuff as u can see 
    #when i need CelerySessionLocal instead of me doing: async with db_manager.CelerySessionLocal as db: i can simaplly async with db_manager.async_session() as db:
    @property
    def celery_session(self):
        self._auto_bootstrap_if_needed()
        return self.CelerySessionLocal

    @property
    def async_session(self):
        self._auto_bootstrap_if_needed()
        return self.AsyncSessionLocal


# Instantiate a single global manager for the application lifecycle
db_manager = DatabaseManager()


def init_cache_database(db_path: str | Path):
    """Initializes engines and session makers using the path provided by the user's system startup."""
    db_manager.initialize(db_path)


async def init_db_tables():
    if db_manager.celery_engine is None: #u may ask this wouldnt happen tho? 
        db_manager._auto_bootstrap_if_needed() #and if it did inside it we are accessing settings() which isnt created on 1st run
        #ur correct! this is here for nth run, where env is alredy created so settings() would exist dw! for cold start!

    async with db_manager.celery_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """Dependency for yielding database sessions safely with automatic lazy loading."""
    SessionMaker = db_manager.async_session
    async with SessionMaker() as session:
        yield session


# PEP 562 Module-Level Dynamic Attributes for legacy code/imports compatibility
def __getattr__(name):
    if name == "CelerySessionLocal":
        return db_manager.celery_session
    if name == "AsyncSessionLocal":
        return db_manager.async_session
    if name == "celery_engine":
        db_manager._auto_bootstrap_if_needed()
        return db_manager.celery_engine
    if name == "norma_engine":
        db_manager._auto_bootstrap_if_needed()
        return db_manager.norma_engine
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")