"""
auth.py -- Authentication API Endpoints
=========================================

This module defines the authentication-related REST API endpoints for the
IMAMU Course Scheduler. All endpoints are prefixed with /api/auth/.

Endpoints:
    POST /api/auth/register -- Create a new user account and return a JWT token.
    POST /api/auth/login    -- Authenticate with username/password and return a JWT token.
    GET  /api/auth/me       -- Return the profile of the currently authenticated user.

Authentication flow:
    1. User registers or logs in via POST request with credentials.
    2. Server validates credentials and returns a JWT access token.
    3. Frontend stores the token (e.g., in localStorage).
    4. For subsequent requests, the frontend includes the token in the
       Authorization header: "Bearer <token>".
    5. Protected endpoints use Depends(get_current_user) to validate the
       token and identify the user.

Architecture note:
    This module only defines the HTTP interface (routes). The actual
    authentication logic (password hashing, token creation/validation) is
    in app/services/auth.py, keeping the routes thin and focused on
    request handling and response formatting.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import User, UserRole
from app.schemas import UserCreate, UserLogin, UserResponse, TokenResponse
from app.services.auth import get_password_hash, verify_password, create_access_token, get_current_user

# ---------------------------------------------------------------------------
# Router Configuration
# ---------------------------------------------------------------------------
# Create an APIRouter with the /api/auth prefix. All endpoints defined in
# this module will automatically have this prefix prepended.
# The "auth" tag groups these endpoints together in the Swagger UI documentation.
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# POST /api/auth/register -- User Registration
# ---------------------------------------------------------------------------
@router.post("/register", response_model=TokenResponse)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user account.

    Validates that the username and email are not already taken, creates
    the user with a bcrypt-hashed password, and returns a JWT token so
    the user is immediately logged in after registration.

    Args:
        user_data: Validated UserCreate schema from the request body.
                   Contains username, email, password, full_name, role, etc.
        db:        Database session injected by FastAPI's dependency system.

    Returns:
        TokenResponse: Contains the JWT access_token and the new user's profile.

    Raises:
        HTTPException 400: If the username or email is already registered.
    """
    # Check for duplicate username
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    # Check for duplicate email
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create the new User ORM object with a hashed password
    user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),  # Hash the plain-text password
        full_name=user_data.full_name,
        role=UserRole(user_data.role),  # Convert string to UserRole enum
        gender=user_data.gender,
        department=user_data.department,
        level=user_data.level,
    )

    # Insert the user into the database
    db.add(user)
    db.commit()       # Persist the new user record
    db.refresh(user)  # Reload from DB to get the auto-generated ID

    # Generate a JWT token with the new user's ID as the subject claim
    token = create_access_token(data={"sub": str(user.id)})

    # Return the token and user profile so the frontend can log in immediately
    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),  # Convert ORM object to Pydantic model
    )


# ---------------------------------------------------------------------------
# POST /api/auth/login -- User Login
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
def login(login_data: UserLogin, db: Session = Depends(get_db)):
    """
    Authenticate a user with username and password.

    Looks up the user by username, verifies the password against the stored
    bcrypt hash, and returns a JWT token on success.

    Args:
        login_data: Validated UserLogin schema containing username and password.
        db:         Database session injected by FastAPI.

    Returns:
        TokenResponse: Contains the JWT access_token and the user's profile.

    Raises:
        HTTPException 401: If the username does not exist or the password
                           is incorrect. A generic error message is returned
                           to avoid leaking whether the username exists.
    """
    # Look up the user by username
    user = db.query(User).filter(User.username == login_data.username).first()

    # Verify the user exists AND the password matches the hash
    # (combined check prevents username enumeration attacks)
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # Generate a JWT token for the authenticated user
    token = create_access_token(data={"sub": str(user.id)})

    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
    )


# ---------------------------------------------------------------------------
# GET /api/auth/me -- Get Current User Profile
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """
    Return the profile of the currently authenticated user.

    This endpoint is used by the frontend to verify that a stored JWT token
    is still valid and to retrieve the user's profile data (role, name, etc.)
    for rendering the UI. It requires a valid Bearer token in the
    Authorization header.

    Args:
        current_user: The authenticated User ORM object, injected by the
                      get_current_user dependency (which validates the JWT).

    Returns:
        UserResponse: The user's profile data (id, username, email, role, etc.).
    """
    return UserResponse.model_validate(current_user)
