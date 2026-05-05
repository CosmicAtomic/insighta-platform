from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index
from database import Base

class Profile(Base):
    __tablename__ = "profiles"
    id = Column(String, primary_key=True)
    name = Column(String, unique= True)
    gender = Column(String, index= True)
    gender_probability = Column(Float, index= True)
    age = Column(Integer, index= True)
    age_group = Column(String, index= True)
    country_id = Column(String, index= True)
    country_name = Column(String)
    country_probability = Column(Float, index= True)
    created_at = Column(DateTime, index= True)
    __table_args__ = (
        Index('ix_profiles_gender_country_age', 'gender', 'country_id', 'age'),
    )

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key= True)
    github_id = Column(String, unique=True)
    username = Column(String)
    email = Column(String)
    avatar_url = Column(String)
    role = Column(String)
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime)
    created_at = Column(DateTime)

class Refresh_Token(Base):
    __tablename__ = "refresh_tokens"
    id = Column(String, primary_key=True)
    token = Column(String, unique=True)
    user_id = Column(String)
    is_used = Column(Boolean, default=False)
    expires_at= Column(DateTime)
    created_at = Column(DateTime)
