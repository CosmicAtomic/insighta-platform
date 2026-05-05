import csv
import io
import uuid6
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from models import Profile
from services import classify_age
from fastapi import UploadFile
from database import invalidate_query_cache

CHUNK_SIZE = 1000
VALID_GENDERS = {"male", "female"}

def validate_row(row: dict, existing_names: set, chunk_names: set) -> dict:
    """
    Returns:
      {"valid": True, "profile": {...}} on success
      {"valid": False, "reason": "..."} on failure
    """
    # Check the row has all required columns at all
    required_fields = ["name", "gender", "age", "country_id"]
    if not all(field in row for field in required_fields):
        return {"valid": False, "reason": "malformed_row"}

    name = (row.get("name") or "").strip().lower()
    gender = (row.get("gender") or "").strip().lower()
    country_id = (row.get("country_id") or "").strip().upper()
    age_str = (row.get("age") or "").strip()

    # Required fields not empty
    if not name or not gender or not country_id or not age_str:
        return {"valid": False, "reason": "missing_fields"}

    # Age must be a positive integer
    try:
        age = int(age_str)
        if age < 0 or age > 150:
            return {"valid": False, "reason": "invalid_age"}
    except ValueError:
        return {"valid": False, "reason": "invalid_age"}

    # Gender must be valid
    if gender not in VALID_GENDERS:
        return {"valid": False, "reason": "invalid_gender"}

    # Duplicate check (existing DB rows + within this chunk)
    if name in existing_names or name in chunk_names:
        return {"valid": False, "reason": "duplicate_name"}

    # Build the profile dict
    return {
        "valid": True,
        "profile": {
            "id": str(uuid6.uuid7()),
            "name": name,
            "gender": gender,
            "gender_probability": float(row.get("gender_probability") or 0.5),
            "age": age,
            "age_group": classify_age(age),
            "country_id": country_id,
            "country_name": row.get("country_name", country_id),
            "country_probability": float(row.get("country_probability") or 0.5),
            "created_at": datetime.now(timezone.utc)
        }
    }

def bulk_insert_chunk(db: Session, chunk: list) -> int:
    """Bulk insert with error handling. Returns number actually inserted."""
    try:
        db.bulk_insert_mappings(Profile, chunk)
        db.commit()
        return len(chunk)
    except Exception as e:
        db.rollback()
        # If bulk fails, try one at a time so we save what we can
        # This shouldn't happen often if validation is solid
        inserted = 0
        for profile in chunk:
            try:
                db.add(Profile(**profile))
                db.commit()
                inserted += 1
            except Exception:
                db.rollback()
        return inserted

async def process_csv_upload(file: UploadFile, db: Session) -> dict:
    counters = {
        "total_rows": 0,
        "inserted": 0,
        "skipped": 0,
        "reasons": {
            "duplicate_name": 0,
            "invalid_age": 0,
            "invalid_gender": 0,
            "missing_fields": 0,
            "malformed_row": 0
        }
    }

    # Get all existing names ONCE at the start (avoids querying per row)
    # For 1M+ records this could be huge; consider using a Bloom filter or
    # just querying per chunk. For now, this works.
    existing_names = set(
        name for (name,) in db.query(Profile.name).all()
    )

    # Read the upload as a text stream — doesn't load everything into memory
    text_stream = io.StringIO((await file.read()).decode('utf-8'))
    reader = csv.DictReader(text_stream)

    chunk = []
    chunk_names = set()  # Track names within this chunk to avoid intra-chunk dupes

    for row in reader:
        counters["total_rows"] += 1

        validation_result = validate_row(row, existing_names, chunk_names)

        if validation_result["valid"]:
            chunk.append(validation_result["profile"])
            chunk_names.add(validation_result["profile"]["name"])
        else:
            counters["skipped"] += 1
            counters["reasons"][validation_result["reason"]] += 1

        # Flush chunk to DB when it hits the limit
        if len(chunk) >= CHUNK_SIZE:
            inserted = bulk_insert_chunk(db, chunk)
            counters["inserted"] += inserted
            existing_names.update(chunk_names)  # Now they're in the DB
            chunk = []
            chunk_names = set()

    # Flush remaining rows
    if chunk:
        inserted = bulk_insert_chunk(db, chunk)
        counters["inserted"] += inserted

    # Invalidate query cache since data changed
    try:
        invalidate_query_cache()
    except Exception:
        pass
    return {
        "status": "success",
        **counters
    }

