from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    # Upper bound stops oversized inputs from being fed to the password hasher.
    password: str = Field(min_length=1, max_length=1024)


class RoleRef(BaseModel):
    id: int
    name: str


class UserOut(BaseModel):
    """The signed-in user at the current property, as the client needs it."""

    id: int
    username: str
    full_name: str
    # None if the user has no role at this property (then permissions is empty).
    role: RoleRef | None
    # Sorted permission keys. For showing/hiding UI only: the server checks every request.
    permissions: list[str]
