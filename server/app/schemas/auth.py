from pydantic import BaseModel, ConfigDict, Field

from app.models import UserRole


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    # Upper bound stops oversized inputs from being fed to the password hasher.
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    role: UserRole
