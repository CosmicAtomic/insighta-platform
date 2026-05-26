import re
import pycountry
import json
import hashlib


def parse_query(query: str) -> dict:
    filters = {}

    # Normalize: lowercase so "Female" and "female" are treated the same
    query = query.lower()

    # Split into individual words for word-level keyword matching
    # e.g. "show me adult males" → ["show", "me", "adult", "males"]
    words = query.split()

    # --- Keyword definitions ---

    # Gender keywords
    female_keywords = {"female", "females", "women", "woman", "girl", "girls"}
    male_keywords = {"male", "males", "men", "man", "boy", "boys"}

    # Age group keywords — each maps to a specific age_group value in the DB
    child_keywords = {"child", "children"}
    teen_keywords = {"teenager", "teenagers", "teen", "teens"}
    adult_keywords = {"adult", "adults"}
    senior_keywords = {"senior", "seniors", "elderly", "old"}
    young_keyword = {"young"}

    # Age comparison phrases — multi-word, so checked against full query string
    # Must be lists (not sets) to preserve order during iteration
    # Multi-word phrases come first to avoid partial matches
    older_patterns = ["older than", "above", "over"]
    younger_patterns = ["younger than", "below", "under"]

    # --- Detect which keywords are present ---

    # any(...) returns True if at least one word in the query matches the keyword set
    has_female = any(word in female_keywords for word in words)
    has_male = any(word in male_keywords for word in words)
    has_child = any(word in child_keywords for word in words)
    has_teen = any(word in teen_keywords for word in words)
    has_adult = any(word in adult_keywords for word in words)
    has_senior = any(word in senior_keywords for word in words)
    has_young = any(word in young_keyword for word in words)

    # --- Gender filter ---
    # If both genders are mentioned (e.g. "men and women"), skip gender filter entirely
    # Check female BEFORE male — "female" contains the word "male", so checking male
    # first would incorrectly match "females"
    if has_female and has_male:
        pass
    elif has_female:
        filters["gender"] = "female"
    elif has_male:
        filters["gender"] = "male"

    # --- Age group filter ---
    # If multiple age group keywords appear (e.g. "senior adult"), the query is
    # ambiguous — skip the filter rather than picking one arbitrarily
    age_group_hits = sum([has_child, has_teen, has_adult, has_senior])
    if age_group_hits == 1:
        if has_child:
            filters["age_group"] = "child"
        elif has_teen:
            filters["age_group"] = "teenager"
        elif has_adult:
            filters["age_group"] = "adult"
        elif has_senior:
            filters["age_group"] = "senior"

    # --- Age comparison: "older than 30", "above 25", "over 18" → min_age ---
    for pattern in older_patterns:
        if pattern in query:
            match = re.search(pattern + r'\s+(\d+)', query)
            if match:
                filters["min_age"] = int(match.group(1))
                break

    # --- Age comparison: "younger than 20", "below 15", "under 30" → max_age ---
    for pattern in younger_patterns:
        if pattern in query:
            match = re.search(pattern + r'\s+(\d+)', query)
            if match:
                filters["max_age"] = int(match.group(1))
                break

    # --- "young" keyword ---
    # Runs after age comparisons so explicit ranges take priority.
    # Only applies if no age group and no explicit age range was extracted.
    # "young adults" → adult wins; "young older than 30" → comparison wins
    if has_young and "age_group" not in filters and "min_age" not in filters and "max_age" not in filters:
        filters["min_age"] = 16
        filters["max_age"] = 24

    # --- Country filter: "from Nigeria" / "from United States" → country_id ---
    # Try progressively shorter phrases after "from" (longest first) so that
    # "from United States" matches before falling back to just "United"
    if "from" in words:
        idx = words.index("from")
        remaining = words[idx + 1:]
        country_found = False
        for length in range(min(len(remaining), 3), 0, -1):
            candidate = " ".join(remaining[:length])
            try:
                results = pycountry.countries.search_fuzzy(candidate)
                if results:
                    filters["country_id"] = results[0].alpha_2
                    country_found = True
                    break
            except LookupError:
                continue

    # Return the filters dict if anything was found, otherwise None
    # None signals that the query couldn't be interpreted
    return filters if filters else None


def normalize_filters(filters: dict) -> dict:
    """
    Convert any filter dict into a canonical form so equivalent queries
    produce the same cache key.

    Rules:
    - Remove None and empty values
    - Lowercase string values where case doesn't matter
    - Uppercase country codes (canonical form)
    - Sort keys alphabetically
    - Convert numeric strings to numbers
    """
    if not filters:
        return {}

    canonical = {}

    # Allowed keys (whitelist to prevent garbage)
    allowed = {
        "gender", "country_id", "age_group",
        "min_age", "max_age",
        "min_gender_probability", "min_country_probability",
        "sort_by", "order", "page", "limit"
    }

    for key, value in filters.items():
        if key not in allowed:
            continue
        if value is None or value == "":
            continue

        # Normalize value formats
        if key == "gender":
            canonical[key] = str(value).lower().strip()
        elif key == "country_id":
            canonical[key] = str(value).upper().strip()
        elif key == "age_group":
            canonical[key] = str(value).lower().strip()
        elif key in ("min_age", "max_age", "page", "limit"):
            canonical[key] = int(value)
        elif key in ("min_gender_probability", "min_country_probability"):
            canonical[key] = float(value)
        elif key == "order":
            v = str(value).lower().strip()
            if v not in ("asc", "desc"):
                continue
            canonical[key] = v
        elif key == "sort_by":
            v = str(value).lower().strip()
            if v not in ("age", "created_at", "gender_probability"):
                continue
            canonical[key] = v
        else:
            canonical[key] = value

    return canonical


def get_cache_key(filters: dict) -> str:
    """Generate a deterministic cache key from filters."""
    normalized = normalize_filters(filters)
    canonical = json.dumps(normalized, sort_keys=True)
    return f"profiles:query:{hashlib.md5(canonical.encode()).hexdigest()}"
