"""Validation utilities for email and password"""
import re

email_regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"


def validate_password(password: str):
    """Validate password strength"""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"\d", password):
        raise ValueError("Password must contain at least one digit")
    if not re.search(r"[A-Za-z]", password):
        raise ValueError("Password must contain at least one letter")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        raise ValueError("Password must contain at least one special character")


def validate_email(email: str):
    """Validate email format"""
    if not re.match(email_regex, email):
        raise ValueError("Invalid email format")
