import re

from django.core.exceptions import ValidationError


GSTIN_PATTERN = re.compile(
    r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$"
)


def validate_gstin(value):
    if value and not GSTIN_PATTERN.fullmatch(value.strip().upper()):
        raise ValidationError("Enter a valid 15-character GSTIN.")
