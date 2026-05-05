import os
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
load_dotenv()

SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sql_app.db")
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=20,            # default is 5
    max_overflow=10,         # extra connections under load
    pool_pre_ping=True,      # check connection before using (handles stale connections)
    pool_recycle=3600        # recycle connections every hour
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def invalidate_query_cache():
    for key in redis_client.scan_iter("profiles:query:*"):
        redis_client.delete(key)
