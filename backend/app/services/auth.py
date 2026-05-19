"""
auth.py -- JWT Authentication and Password Hashing Service
==========================================================

This module provides the security layer for the IMAMU Course Scheduler.
It handles:

1. **Password Hashing** -- Securely hashing user passwords with bcrypt
   before storing them in the database, and verifying passwords during login.

2. **JWT Token Creation** -- Generating signed JSON Web Tokens (JWTs) that
   encode the user's ID and an expiration timestamp. The frontend stores
   this token and sends it in the Authorization header for authenticated requests.

3. **JWT Token Validation** -- Decoding and verifying incoming tokens to
   identify the requesting user. Invalid or expired tokens result in a
   401 Unauthorized response.

4. **FastAPI Dependency** -- get_current_user() is a dependency that can be
   injected into any route to enforce authentication. It extracts the Bearer
   token from the request header, validates it, and returns the User ORM object.

Security overview:
    - Passwords are hashed using bcrypt (via passlib), which is a slow,
      salted hashing algorithm designed to resist brute-force attacks.
    - JWTs are signed with HS256 (HMAC-SHA256), a symmetric algorithm where
      the same SECRET_KEY is used for both signing and verification.
    - Token expiry is enforced by the 'exp' claim in the JWT payload.

Dependencies:
    - python-jose[cryptography]: JWT encoding/decoding.
    - passlib[bcrypt]: Password hashing.
    - FastAPI's OAuth2PasswordBearer: Extracts Bearer tokens from requests.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models.models import User

# ---------------------------------------------------------------------------
# Password Hashing Context
# ---------------------------------------------------------------------------
# CryptContext manages password hashing and verification. The "bcrypt" scheme
# is the only active scheme; "deprecated='auto'" means that if we ever add a
# new scheme in the future, bcrypt hashes will be automatically rehashed on
# next verification (seamless migration).
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# OAuth2 Bearer Token Scheme
# ---------------------------------------------------------------------------
# This tells FastAPI where to find the token: in the Authorization header
# as "Bearer <token>". The tokenUrl points to the login endpoint, which is
# used by the auto-generated Swagger UI to obtain tokens interactively.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain_password, hashed_password):
    """
    Verify a plain-text password against a bcrypt hash.

    Uses passlib's constant-time comparison to prevent timing attacks.

    Args:
        plain_password:  The password the user typed during login.
        hashed_password: The bcrypt hash stored in the database.

    Returns:
        bool: True if the password matches the hash, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    """
    Hash a plain-text password using bcrypt.

    bcrypt automatically generates a random salt and includes it in the
    resulting hash string, so no separate salt storage is needed.

    Args:
        password: The plain-text password to hash.

    Returns:
        str: The bcrypt hash string (e.g., "$2b$12$...").
    """
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """
    Create a signed JWT access token.

    The token payload contains:
    - 'sub' (subject): The user's database ID, stored as a string.
    - 'exp' (expiration): UTC timestamp after which the token is invalid.

    The token is signed with the application's SECRET_KEY using HS256.
    Anyone with the SECRET_KEY can verify the token, but without it,
    the token cannot be forged.

    Args:
        data:          Dictionary of claims to include in the token.
                       Typically {"sub": str(user.id)}.
        expires_delta: Optional custom token lifetime. If None, uses the
                       default from settings.ACCESS_TOKEN_EXPIRE_MINUTES.

    Returns:
        str: The encoded JWT string.
    """
    # Copy the data to avoid mutating the caller's dictionary
    to_encode = data.copy()

    # Calculate expiration time (default: 24 hours from now)
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))

    # Add the expiration claim to the payload
    to_encode.update({"exp": expire})

    # Sign and encode the token
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """
    FastAPI dependency that authenticates the current request.

    This function is injected into route handlers via Depends(get_current_user).
    It performs the following steps:

    1. Extracts the Bearer token from the Authorization header (handled by
       oauth2_scheme).
    2. Decodes the JWT and extracts the 'sub' (subject) claim, which contains
       the user's database ID.
    3. Queries the database for the user with that ID.
    4. If any step fails (expired token, invalid signature, missing user),
       raises a 401 Unauthorized exception.

    Args:
        token: The JWT Bearer token, automatically extracted by FastAPI.
        db:    The database session, automatically injected by FastAPI.

    Returns:
        User: The authenticated User ORM object.

    Raises:
        HTTPException: 401 Unauthorized if the token is invalid, expired,
                       or the user no longer exists in the database.
    """
    # Prepare the exception to raise if authentication fails
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},  # OAuth2 spec requires this header
    )

    try:
        # Decode the JWT token and verify its signature and expiration
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

        # Extract the user ID from the 'sub' (subject) claim
        user_id_raw = payload.get("sub")
        if user_id_raw is None:
            raise credentials_exception  # Token has no subject claim

        # Convert the subject to an integer (user IDs are integers)
        user_id = int(user_id_raw)
    except (JWTError, ValueError):
        # JWTError: token is malformed, expired, or has an invalid signature
        # ValueError: 'sub' claim is not a valid integer
        raise credentials_exception

    # Look up the user in the database by their ID
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception  # User was deleted after the token was issued

    return user
