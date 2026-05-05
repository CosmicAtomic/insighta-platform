from database import SessionLocal
from models import Profile
import json
import uuid6
from datetime import datetime, timezone

with open('seed_profiles.json', 'r', encoding='utf-8') as file:
    data = json.load(file)

db = SessionLocal()
existing_names = {profile.name for profile in db.query(Profile.name).all()}

for profile in data["profiles"]:
    if profile["name"] not in existing_names:
        new_profile = Profile(
            id=str(uuid6.uuid7()),
            name=profile["name"],
            gender=profile["gender"],
            gender_probability=profile["gender_probability"],
            age=profile["age"],
            age_group=profile["age_group"],
            country_id=profile["country_id"],
            country_name=profile["country_name"],
            country_probability=profile["country_probability"],
            created_at=datetime.now(timezone.utc)
        )
        db.add(new_profile)

db.commit()
db.close()
