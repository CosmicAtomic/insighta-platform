from pydantic import BaseModel, field_validator


class ProfileRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        v = v.strip().lower()
        if not v:
            raise ValueError("name must not be empty")
        return v


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenExchangeRequest(BaseModel):
    code: str
    code_verifier: str
