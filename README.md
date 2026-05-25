# Insighta Platform API

A REST API that accepts a name and returns a rich demographic profile by aggregating data from three public APIs — gender prediction, age estimation, and nationality inference. Profiles are persisted in a PostgreSQL database and exposed through a set of authenticated endpoints that support filtering, sorting, pagination, natural language search, and CSV export.

---

## Live URL

> `https://insighta-gfrf.onrender.com/`

---

## Tech Stack

- **Python** + **FastAPI**
- **SQLAlchemy** (ORM)
- **PostgreSQL** (production) / SQLite (local fallback)
- **Redis** (query result caching)
- **Pydantic** (request validation)
- **httpx** (async HTTP client)
- **PyJWT** (token encoding and verification)
- **uuid6** (UUID v7 generation)
- **pycountry** (country name and code lookup)

---

## System Architecture

The application is split into focused modules, each with a single responsibility:

```
main.py              — App entry point: registers routers, middleware, and exception handlers
database.py          — SQLAlchemy engine, session factory, Redis client, and get_db dependency
models.py            — ORM table definitions (Profile, User, Refresh_Token)
schemas.py           — Pydantic request/response models
services.py          — External API calls (Genderize, Agify, Nationalize) and helper functions
utils.py             — Response formatters and pagination link builder
query_parser.py      — Rule-based natural language query parser + cache key normalization
auth.py              — JWT utilities, get_current_user dependency, require_role guard
profile_routes.py    — All /api/profiles endpoints
auth_routes.py       — All /auth endpoints (OAuth, token refresh, logout, /me)
csv_ingestion.py     — Streaming CSV bulk upload with chunked inserts and row validation
seed.py              — Database seeding script
```

**Request lifecycle (protected route):**

```
Client Request
    → X-API-Version header check (check_api_version)
    → Authorization header parsed (get_current_user)
    → JWT verified, user fetched from DB
    → Role checked if required (require_role)
    → Route handler executes
    → JSONResponse returned
```

**Data flow for profile creation:**

```
POST /api/profiles
    → Genderize.io  → gender + probability
    → Agify.io      → age
    → Nationalize.io → country list → highest probability country selected
    → age classified into group (child / teenager / adult / senior)
    → country code resolved to full name via pycountry
    → Profile saved to DB with UUID v7
    → Redis query cache invalidated
```

---

## External APIs Used

| API | Purpose | Endpoint |
|-----|---------|----------|
| [Genderize.io](https://genderize.io) | Predicts gender from name | `https://api.genderize.io?name={name}` |
| [Agify.io](https://agify.io) | Predicts age from name | `https://api.agify.io?name={name}` |
| [Nationalize.io](https://nationalize.io) | Predicts nationality from name | `https://api.nationalize.io?name={name}` |

---

## Running Locally

### 1. Clone the repository

```bash
git clone https://github.com/CosmicAtomic/insighta_platform.git
cd insighta_platform
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Mac/Linux
.venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up environment variables

Copy `.env.example` to `.env` and fill in your values:

```env
DATABASE_URL=postgresql://username:password@localhost:5432/insighta
REDIS_URL=redis://localhost:6379
GITHUB_CLIENT_ID=your_github_oauth_app_client_id
GITHUB_CLIENT_SECRET=your_github_oauth_app_client_secret
CALLBACK_URL=http://localhost:8000/auth/github/callback
JWT_SECRET_KEY=some_long_random_string
```

If `DATABASE_URL` is not set, the app falls back to a local SQLite database (`sql_app.db`).  
If `REDIS_URL` is not set, it defaults to `redis://localhost:6379`. Caching will silently fail if Redis is unavailable — the API still works, just without caching.

### 5. Start the server

```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.

---

## Seeding the Database

```bash
DATABASE_URL=your_database_url python seed.py
```

The script loads profiles from `seed_profiles.json`. It checks existing names before inserting, so it is safe to run multiple times.

---

## Authentication Flow

Authentication is handled via GitHub OAuth. No passwords are stored.

```
1. Client visits GET /auth/github  (or GET /auth/github?redirect_to=<frontend_url>)
      → Server generates a random state token and PKCE code verifier
      → Both are stored in memory keyed by state
      → Client is redirected to GitHub's authorization page

2. GitHub redirects back to GET /auth/github/callback?code=...&state=...
      → Server validates the state matches a known pending request
      → Server exchanges the code + code_verifier for a GitHub access token
      → Server calls GET https://api.github.com/user to fetch profile data

3. User lookup / creation
      → If github_id exists in users table: update last_login_at
      → If not: create new user with role = "analyst"

4. Token issuance
      → Access token generated (JWT, 3 min expiry)
      → Refresh token generated (JWT, 5 min expiry), stored in refresh_tokens table
      → Both returned to client (JSON or redirect with tokens in query params)
```

**Redirect flow** (for frontend clients):

```
GET /auth/github?redirect_to=https://insighta-lab.netlify.app/dashboard.html
```

After login, the server redirects to:
```
https://insighta-lab.netlify.app/dashboard.html?access_token=...&refresh_token=...&username=...
```

---

## Token Handling Approach

The API uses two JWTs per session — a short-lived access token and a longer-lived refresh token.

**Access token:**
- Signed with `HS256` using `JWT_SECRET_KEY`
- Payload: `{ user_id, role, exp, iat }`
- Expires in **15 minutes**
- Sent in the `Authorization: Bearer <token>` header on every request
- Never stored in the database

**Refresh token:**
- Signed with `HS256` using the same key
- Payload: `{ user_id, exp, iat }`
- Expires in **20 minutes**
- Stored in the `refresh_tokens` table with an `is_used` flag
- One-time use — marked `is_used = True` immediately on use

**Token refresh flow:**
1. Client sends `POST /auth/refresh` with the refresh token in the request body
2. Server verifies the JWT signature and expiry
3. Server checks the token exists in the DB and `is_used = False`
4. Old token is marked used, new access + refresh tokens are issued and stored
5. Client replaces both tokens

**Logout:**
- Client sends `POST /auth/logout` with the refresh token
- Server marks it `is_used = True`
- The access token is stateless so it remains valid until it naturally expires (max 15 minutes)

---

## Role Enforcement Logic

Every user is assigned one of two roles at account creation:

| Role | Assigned | Permissions |
|------|----------|-------------|
| `analyst` | Default for all new GitHub logins | Read-only access — can list, search, filter, export, and view profiles |
| `admin` | Manually assigned in the database | Full access — all analyst permissions plus create, upload, and delete profiles |

All `/api/profiles` routes require the `X-API-Version: 1` header and a valid JWT.

```
GET  /api/profiles          → any authenticated user
GET  /api/profiles/export   → any authenticated user
GET  /api/profiles/search   → any authenticated user
GET  /api/profiles/{id}     → any authenticated user
POST /api/profiles          → admin only
POST /api/profiles/upload   → admin only
DELETE /api/profiles/{id}   → admin only
```

---

## API Endpoints

### Auth

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/auth/github` | None | Initiates GitHub OAuth login |
| `GET` | `/auth/github/callback` | None | GitHub OAuth callback |
| `POST` | `/auth/refresh` | None | Exchange refresh token for new tokens |
| `POST` | `/auth/logout` | None | Invalidate a refresh token |
| `GET` | `/auth/me` | Bearer token | Get current user info |
| `GET` | `/api/users/me` | Bearer token | Alias for /auth/me |

### Profiles

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/profiles` | Admin | Create a new profile |
| `GET` | `/api/profiles` | Any user | List profiles with filtering, sorting, pagination |
| `GET` | `/api/profiles/export` | Any user | Export filtered profiles as CSV |
| `GET` | `/api/profiles/search` | Any user | Natural language search |
| `GET` | `/api/profiles/{id}` | Any user | Get a single profile by ID |
| `DELETE` | `/api/profiles/{id}` | Admin | Delete a profile |
| `POST` | `/api/profiles/upload` | Admin | Bulk upload profiles from CSV |

All profile endpoints require the `X-API-Version: 1` header.

---

### `GET /api/profiles` — Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `gender` | string | `male` or `female` |
| `country_id` | string | ISO 3166-1 alpha-2 code (e.g. `NG`) |
| `age_group` | string | `child`, `teenager`, `adult`, or `senior` |
| `min_age` | integer | Minimum age (inclusive) |
| `max_age` | integer | Maximum age (inclusive) |
| `min_gender_probability` | float | Minimum gender confidence score |
| `min_country_probability` | float | Minimum country confidence score |
| `sort_by` | string | `age`, `created_at`, or `gender_probability` |
| `order` | string | `asc` or `desc` (default: `asc`) |
| `page` | integer | Page number (default: 1) |
| `limit` | integer | Results per page (default: 10, max: 50) |

**Response envelope:**
```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 120,
  "total_pages": 12,
  "links": {
    "self": "/api/profiles?page=1&limit=10",
    "next": "/api/profiles?page=2&limit=10",
    "prev": null
  },
  "data": [
    {
      "id": "01906b2a-...",
      "name": "James",
      "gender": "male",
      "gender_probability": 0.98,
      "age": 34,
      "age_group": "adult",
      "country_id": "US",
      "country_name": "United States",
      "country_probability": 0.12,
      "created_at": "2025-05-20T10:00:00Z"
    }
  ]
}
```

---

## Age Group Classification

| Age Range | Group |
|-----------|-------|
| 0 – 12 | `child` |
| 13 – 19 | `teenager` |
| 20 – 59 | `adult` |
| 60+ | `senior` |

---

## Natural Language Search

The `/api/profiles/search?q=<query>` endpoint uses a **rule-based keyword scanner** — no AI or LLM involved.

**Examples:**
```
?q=adult males from Nigeria
?q=women older than 30
?q=young females
?q=senior men from Japan
```

**Supported keywords:**

| Type | Keywords | Filter applied |
|------|----------|----------------|
| Gender | `male`, `males`, `men`, `man` | `gender = "male"` |
| Gender | `female`, `females`, `women`, `woman` | `gender = "female"` |
| Age group | `child`, `children` | `age_group = "child"` |
| Age group | `teen`, `teens`, `teenager`, `teenagers` | `age_group = "teenager"` |
| Age group | `adult`, `adults` | `age_group = "adult"` |
| Age group | `senior`, `seniors`, `elderly`, `old` | `age_group = "senior"` |
| Age range | `young` (no other age group) | `min_age=16, max_age=24` |
| Min age | `older than N`, `above N`, `over N` | `min_age = N` |
| Max age | `younger than N`, `below N`, `under N` | `max_age = N` |
| Country | `from <country>` | `country_id = <ISO code>` |

Returns the same response envelope as `GET /api/profiles`.

---

## Error Responses

```json
{ "status": "error", "message": "<description>" }
```

| Status Code | Cause |
|-------------|-------|
| `400` | Missing/empty name, uninterpretable search query, missing API version header |
| `401` | Missing, expired, or invalid token |
| `403` | Authenticated but insufficient role, or inactive account |
| `404` | Profile not found |
| `422` | Invalid query parameter type or value |
| `429` | Rate limit exceeded — retry after 60 seconds |
| `502` | External API (Genderize/Agify/Nationalize) returned unusable data |

---

## Frontend Integration Guide

This section is for the frontend developer building on top of this API.

### Base URL

```
https://insighta-gfrf.onrender.com
```

Store this as a constant — never hardcode it across multiple files.

```js
const API_BASE = "https://insighta-gfrf.onrender.com";
```

---

### Step 1 — GitHub OAuth Login

There are no username/password credentials. Login is handled entirely through GitHub OAuth.

**Trigger login** by redirecting the user to:

```
https://insighta-gfrf.onrender.com/auth/github?redirect_to=https://insighta-lab.netlify.app/dashboard.html
```

After the user approves the OAuth prompt on GitHub, the backend redirects them back to:

```
https://insighta-lab.netlify.app/dashboard.html?access_token=<token>&refresh_token=<token>&username=<username>
```

**On your dashboard page, read the tokens from the URL:**

```js
const params = new URLSearchParams(window.location.search);
const accessToken = params.get("access_token");
const refreshToken = params.get("refresh_token");
const username = params.get("username");

// Store them — see token storage section below
localStorage.setItem("access_token", accessToken);
localStorage.setItem("refresh_token", refreshToken);

// Clean the tokens from the URL bar
window.history.replaceState({}, document.title, window.location.pathname);
```

---

### Step 2 — Required Headers

Every call to a `/api/profiles` endpoint requires **two headers**:

```
X-API-Version: 1
Authorization: Bearer <access_token>
```

Build a reusable fetch helper so you don't repeat this:

```js
function apiRequest(path, options = {}) {
  const token = localStorage.getItem("access_token");
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-API-Version": "1",
      "Authorization": `Bearer ${token}`,
      ...(options.headers || {})
    }
  });
}
```

Auth endpoints (`/auth/refresh`, `/auth/logout`, `/auth/me`) do **not** require `X-API-Version`.

---

### Step 3 — Token Storage

| Method | Recommendation |
|--------|----------------|
| `localStorage` | Simple, works fine for this project. Tokens expire in 3–5 min so the exposure window is small. |
| `sessionStorage` | Slightly safer — tokens are cleared when the tab closes. |
| Cookie (httpOnly) | Safest against XSS but requires server-side changes. Not currently supported. |

Since tokens expire in 15 minutes, don't overthink this — `localStorage` is fine.

---

### Step 4 — Token Refresh Strategy

Access tokens expire after **15 minutes**. When a request returns `401`, refresh the token and retry.

```js
async function apiRequestWithRefresh(path, options = {}) {
  let response = await apiRequest(path, options);

  if (response.status === 401) {
    const refreshed = await refreshAccessToken();
    if (!refreshed) {
      // Refresh also failed — send user back to login
      redirectToLogin();
      return null;
    }
    response = await apiRequest(path, options); // retry once
  }

  return response;
}

async function refreshAccessToken() {
  const refreshToken = localStorage.getItem("refresh_token");
  if (!refreshToken) return false;

  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken })
  });

  if (!res.ok) {
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    return false;
  }

  const data = await res.json();
  localStorage.setItem("access_token", data.access_token);
  localStorage.setItem("refresh_token", data.refresh_token);
  return true;
}

function redirectToLogin() {
  window.location.href =
    `${API_BASE}/auth/github?redirect_to=https://insighta-lab.netlify.app/dashboard.html`;
}
```

> **Note:** Refresh tokens also expire after 20 minutes and are single-use. Each `/auth/refresh` call returns a brand-new pair of tokens — always replace both.

---

### Step 5 — Logout

```js
async function logout() {
  const refreshToken = localStorage.getItem("refresh_token");
  await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken })
  });
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  window.location.href = "/";
}
```

---

### Step 6 — Get Current User

```js
const res = await apiRequest("/auth/me");
const { data } = await res.json();
// data.id, data.username, data.email, data.role, data.avatar_url
```

Response:
```json
{
  "status": "success",
  "data": {
    "id": "01906b2a-...",
    "github_id": "12345678",
    "username": "johndoe",
    "email": "john@example.com",
    "role": "analyst",
    "avatar_url": "https://avatars.githubusercontent.com/...",
    "is_active": true,
    "last_login_at": "2025-05-20T10:00:00Z",
    "created_at": "2025-05-01T08:00:00Z"
  }
}
```

Use `data.role` to conditionally show/hide admin controls in the UI.

---

### API Usage Examples (JavaScript)

**List profiles with filters:**
```js
const res = await apiRequestWithRefresh(
  "/api/profiles?gender=female&age_group=adult&page=1&limit=20"
);
const data = await res.json();
// data.data → array of profiles
// data.total, data.total_pages, data.links.next, data.links.prev
```

**Get a single profile:**
```js
const res = await apiRequestWithRefresh(`/api/profiles/${id}`);
const { data } = await res.json();
```

**Natural language search:**
```js
const query = encodeURIComponent("adult females from Nigeria");
const res = await apiRequestWithRefresh(`/api/profiles/search?q=${query}`);
const data = await res.json();
```

**Export as CSV (triggers file download):**
```js
const res = await apiRequestWithRefresh("/api/profiles/export?gender=male");
const blob = await res.blob();
const url = URL.createObjectURL(blob);
const a = document.createElement("a");
a.href = url;
a.download = "profiles.csv";
a.click();
```

**Create a profile (admin only):**
```js
const res = await apiRequestWithRefresh("/api/profiles", {
  method: "POST",
  body: JSON.stringify({ name: "Amara" })
});
```

**Delete a profile (admin only):**
```js
const res = await apiRequestWithRefresh(`/api/profiles/${id}`, {
  method: "DELETE"
});
// 204 No Content on success
```

---

### CORS

The API accepts requests from any origin (`*`). No special CORS setup is needed on the frontend. Requests with credentials (cookies) are not supported — use the `Authorization` header instead.

---

### Rate Limits

| Endpoint | Limit |
|----------|-------|
| `GET /auth/github` | 10 requests/minute |
| All other `/auth/*` endpoints | 30 requests/minute |

When rate limited, the API returns `429` with header `Retry-After: 60`. Wait 60 seconds before retrying.

---

### GitHub OAuth App Setup (if running locally)

To run the full auth flow locally, you need your own GitHub OAuth App:

1. Go to GitHub → Settings → Developer Settings → OAuth Apps → New OAuth App
2. Set **Homepage URL** to `http://localhost:8000`
3. Set **Authorization callback URL** to `http://localhost:8000/auth/github/callback`
4. Copy the **Client ID** and **Client Secret** into your `.env`

For local frontend development, change the `redirect_to` to your local frontend URL (e.g. `http://localhost:3000/dashboard.html`).

---

## Related Repositories

- **CLI Tool:** [insighta-cli](https://github.com/CosmicAtomic/insighta-cli) — Terminal interface for all API operations
- **Web Portal:** [insighta-web](https://github.com/CosmicAtomic/insighta-web) — Browser-based dashboard at https://insighta-lab.netlify.app/

Both clients authenticate through this backend via GitHub OAuth and consume the same API endpoints.

---

## CLI Usage

All protected endpoints require two headers on every request:

```
X-API-Version: 1
Authorization: Bearer <access_token>
```

**Get tokens (test mode):**
```bash
curl https://insighta-gfrf.onrender.com/auth/github/callback?code=test_code
```

**List profiles:**
```bash
curl -H "X-API-Version: 1" \
     -H "Authorization: Bearer <access_token>" \
     https://insighta-gfrf.onrender.com/api/profiles
```

**Refresh an expired access token:**
```bash
curl -X POST https://insighta-gfrf.onrender.com/auth/refresh \
     -H "Content-Type: application/json" \
     -d '{"refresh_token": "<refresh_token>"}'
```

**Logout:**
```bash
curl -X POST https://insighta-gfrf.onrender.com/auth/logout \
     -H "Content-Type: application/json" \
     -d '{"refresh_token": "<refresh_token>"}'
```

**Export profiles as CSV:**
```bash
curl -H "X-API-Version: 1" \
     -H "Authorization: Bearer <access_token>" \
     "https://insighta-gfrf.onrender.com/api/profiles/export?gender=female&age_group=adult" \
     -o profiles.csv
```
