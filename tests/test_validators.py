import pytest

from shared.utils.validators import validate_hex12


def test_valid_hex12():
    assert validate_hex12("A1B2C3D4E5F6", "tbox_code") == "A1B2C3D4E5F6"


def test_lowercase_is_uppercased():
    assert validate_hex12("a1b2c3d4e5f6", "sensor_code") == "A1B2C3D4E5F6"


def test_too_short_raises():
    with pytest.raises(ValueError, match="sensor_code"):
        validate_hex12("A1B2C3", "sensor_code")


def test_too_long_raises():
    with pytest.raises(ValueError):
        validate_hex12("A1B2C3D4E5F6FF", "tbox_code")


def test_invalid_chars_raises():
    with pytest.raises(ValueError):
        validate_hex12("A1B2C3D4E5GG", "tbox_code")
