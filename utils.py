def format_full_profile(profile):
    return {
        "id": profile.id,
        "name": profile.name,
        "gender": profile.gender,
        "gender_probability": profile.gender_probability,
        "age": profile.age,
        "age_group": profile.age_group,
        "country_id": profile.country_id,
        "country_name": profile.country_name,
        "country_probability": profile.country_probability,
        "created_at": profile.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    }


def get_page_links(current_page, page_limit, total_page):
    next_page = current_page + 1 if current_page < total_page else None
    previous_page = current_page - 1 if current_page > 1 else None
    return {
        "self": f"/api/profiles?page={current_page}&limit={page_limit}",
        "next": f"/api/profiles?page={next_page}&limit={page_limit}" if next_page else None,
        "prev": f"/api/profiles?page={previous_page}&limit={page_limit}" if previous_page else None
    }
