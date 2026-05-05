import io
import uuid6
import math
import csv
import json
from typing import Optional
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status, Response, UploadFile, File
from sqlalchemy.orm import Session
from schemas import ProfileRequest
from models import Profile
from database import get_db, redis_client, invalidate_query_cache
from utils import format_full_profile, get_page_links
from services import (get_agify_data, get_genderize_data, get_nationalize_data,
                      choose_country, classify_age, get_country_name)
from query_parser import parse_query, get_cache_key
from auth import get_current_user, require_role, check_api_version
from csv_ingestion import process_csv_upload

profile_router = APIRouter(prefix="/api/profiles", dependencies=[Depends(check_api_version)])

SORT_FIELD = {
    "age": Profile.age,
    "created_at": Profile.created_at,
    "gender_probability": Profile.gender_probability
}


@profile_router.post('')
async def create_profile(
    profile_request: ProfileRequest,
    db: Session = Depends(get_db),
    current_user=Depends(
        require_role("admin"))):
    name = profile_request.name
    existing_profile = db.query(Profile).filter(Profile.name == name).first()
    if existing_profile:
        return JSONResponse(content={
            "status": "success",
            "message": "Profile already exists",
            "data": format_full_profile(existing_profile)
        }
        )

    genderize = await get_genderize_data(name)
    if genderize["count"] == 0 or genderize["gender"] is None:
        return JSONResponse(
            status_code=502,
            content={"status": "502", "message": "Genderize returned an invalid response"}
        )

    agify = await get_agify_data(name)
    if agify["age"] is None:
        return JSONResponse(
            status_code=502,
            content={"status": "502", "message": "Agify returned an invalid response"}
        )

    nationalize = await get_nationalize_data(name)
    if not nationalize["country"]:
        return JSONResponse(
            status_code=502,
            content={"status": "502", "message": "Nationalize returned an invalid response"}
        )

    age_class = classify_age(agify["age"])
    choice_country = choose_country(nationalize["country"])
    country_id = choice_country["country_id"]
    country_name = get_country_name(country_id)

    new_profile = Profile(
        id=str(uuid6.uuid7()),
        name=name,
        gender=genderize["gender"],
        gender_probability=genderize["probability"],
        age=agify["age"],
        age_group=age_class,
        country_id=country_id,
        country_name=country_name,
        country_probability=choice_country["probability"],
        created_at=datetime.now(timezone.utc)
    )
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)

    try:
        invalidate_query_cache()
    except Exception:
        pass

    return JSONResponse(
        status_code=201,
        content={
            "status": "success",
            "data": format_full_profile(new_profile)
        }
    )


@profile_router.get('')
def get_profiles(
        gender: Optional[str] = None,
        country_id: Optional[str] = None,
        age_group: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        min_gender_probability: Optional[float] = None,
        min_country_probability: Optional[float] = None,
        sort_by: Optional[str] = None,
        order: Optional[str] = None,
        page: int = 1,
        limit: int = 10,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user)):
    if sort_by is not None and sort_by.lower() not in ("age", "created_at", "gender_probability"):
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )
    if order is not None and order.lower() not in ("asc", "desc"):
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )
    order = (order or "asc").lower()
    limit = min(limit, 50)
    skip = (page - 1) * limit

    params = {
        "gender": gender,
        "country_id": country_id,
        "age_group": age_group,
        "min_age": min_age,
        "max_age": max_age,
        "min_gender_probability": min_gender_probability,
        "min_country_probability": min_country_probability,
        "sort_by": sort_by,
        "order": order,
        "page": page,
        "limit": limit
    }
    # Remove None values so they don't affect the key
    params = {k: v for k, v in params.items() if v is not None}

    cache_key = get_cache_key(params)

    # Try cache first
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return JSONResponse(content=json.loads(cached))
    except Exception:
        pass

    query = db.query(Profile)
    if gender:
        query = query.filter(Profile.gender == gender.lower())
    if country_id:
        query = query.filter(Profile.country_id == country_id.upper())
    if age_group:
        query = query.filter(Profile.age_group == age_group.lower())
    if min_age is not None:
        query = query.filter(Profile.age >= min_age)
    if max_age is not None:
        query = query.filter(Profile.age <= max_age)
    if min_gender_probability is not None:
        query = query.filter(Profile.gender_probability >= min_gender_probability)
    if min_country_probability is not None:
        query = query.filter(Profile.country_probability >= min_country_probability)

    total = query.count()
    sort_column = SORT_FIELD.get(sort_by, Profile.created_at)
    if order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    profiles = query.offset(skip).limit(limit)

    total_pages = math.ceil(total / limit)

    response_dict = {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": get_page_links(page, limit, total_pages),
        "data": [format_full_profile(profile) for profile in profiles],
    }

    try:
        redis_client.setex(cache_key, 300, json.dumps(response_dict))  # 5 min TTL
    except Exception:
        pass
    return JSONResponse(status_code=200, content=response_dict)


@profile_router.get('/export')
def export_profiles_csv(
        gender: Optional[str] = None,
        country_id: Optional[str] = None,
        age_group: Optional[str] = None,
        min_age: Optional[int] = None,
        max_age: Optional[int] = None,
        min_gender_probability: Optional[float] = None,
        min_country_probability: Optional[float] = None,
        sort_by: Optional[str] = None,
        order: Optional[str] = None,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user)):

    if sort_by is not None and sort_by.lower() not in ("age", "created_at", "gender_probability"):
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )
    if order is not None and order.lower() not in ("asc", "desc"):
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )

    order = (order or "asc").lower()

    query = db.query(Profile)
    if gender:
        query = query.filter(Profile.gender == gender.lower())
    if country_id:
        query = query.filter(Profile.country_id == country_id.upper())
    if age_group:
        query = query.filter(Profile.age_group == age_group.lower())
    if min_age is not None:
        query = query.filter(Profile.age >= min_age)
    if max_age is not None:
        query = query.filter(Profile.age <= max_age)
    if min_gender_probability is not None:
        query = query.filter(Profile.gender_probability >= min_gender_probability)
    if min_country_probability is not None:
        query = query.filter(Profile.country_probability >= min_country_probability)

    sort_column = SORT_FIELD.get(sort_by, Profile.created_at)
    if order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    profiles = [format_full_profile(profile) for profile in query]

    # 2. Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    # Header
    writer.writerow(["id", "name", "gender", "gender_probability", "age", "age_group",
                    "country_id", "country_name", "country_probability", "created_at"])

    for p in profiles:
        writer.writerow([
            p["id"], p["name"], p["gender"], p["gender_probability"],
            p["age"], p["age_group"], p["country_id"], p["country_name"],
            p["country_probability"], p["created_at"]
        ])

    # 3. Return Response
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=profiles_{timestamp}.csv"
        }
    )


@profile_router.get('/search')
def search(
        q: str,
        page: int = 1,
        limit: int = 10,
        db: Session = Depends(get_db),
        current_user=Depends(get_current_user)):
    if not q:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Search query cannot be empty"}
        )

    filter = parse_query(q)
    if not filter:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Unable to interpret query"}
        )
    filter["page"] = page
    filter["limit"] = min(limit, 50)

    cache_key = get_cache_key(filter)

    try:
        cached = redis_client.get(cache_key)
        if cached:
            return JSONResponse(content=json.loads(cached))
    except Exception:
        pass

    query = db.query(Profile)
    if filter.get("gender"):
        query = query.filter(Profile.gender == filter["gender"])
    if filter.get("age_group"):
        query = query.filter(Profile.age_group == filter["age_group"])
    if filter.get("min_age") is not None:
        query = query.filter(Profile.age >= filter["min_age"])
    if filter.get("max_age") is not None:
        query = query.filter(Profile.age <= filter["max_age"])
    if filter.get("country_id"):
        query = query.filter(Profile.country_id == filter["country_id"])

    total = query.count()
    limit = min(limit, 50)
    skip = (page - 1) * limit
    total_pages = math.ceil(total / limit)

    profiles = query.offset(skip).limit(limit)

    response_dict = {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": get_page_links(page, limit, total_pages),
        "data": [format_full_profile(profile) for profile in profiles],
    }
    try:
        redis_client.setex(cache_key, 300, json.dumps(response_dict))
    except Exception:
        pass

    return JSONResponse(status_code=200, content=response_dict)


@profile_router.post('/upload')
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(
        require_role("admin"))):
    if not file.filename.endswith('.csv'):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "File must be a CSV"}
        )
    result = await process_csv_upload(file, db)
    return JSONResponse(content=result)


@profile_router.get("/{id}")
def get_profile(id: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    profile = db.query(Profile).filter(Profile.id == id).first()

    if not profile:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Profile not found"}
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "data": format_full_profile(profile)
        }
    )


@profile_router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(id: str, db: Session = Depends(get_db), current_user=Depends(require_role("admin"))):
    profile = db.query(Profile).filter(Profile.id == id).first()
    if not profile:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Profile not found"}
        )
    db.delete(profile)
    db.commit()
    try:
        invalidate_query_cache()
    except Exception:
        pass

    return Response(status_code=status.HTTP_204_NO_CONTENT)
