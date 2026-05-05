import time
import logging
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from limiter import limiter
from profile_routes import profile_router
from database import Base, engine
from auth_routes import auth_router, users_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

app = FastAPI()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000, 2)
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({duration}ms)")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

app.state.limiter = limiter


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": "60"},
        content={"status": "error", "message": "Too many requests. Please try again later."}
    )

app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.include_router(profile_router)
app.include_router(auth_router)
app.include_router(users_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "message": str(exc.detail)}
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    for error in exc.errors():
        if error["type"] in ("missing", "value_error") and "name" in error.get("loc", []):
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Missing or empty name"}
            )
        if error["type"] in ("int_parsing", "float_parsing"):
            return JSONResponse(
                status_code=422,
                content={"status": "error", "message": "Invalid query parameters"}
            )
        if error["type"] == "missing":
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Missing required parameter"}
            )
    return JSONResponse(
        status_code=422,
        content={"status": "error", "message": "Invalid type"}
    )


@app.get('/')
def test():
    return JSONResponse(content={"message": "API is running"})
