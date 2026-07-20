from trace_viewer.redaction import redact


def test_redacts_sensitive_keys():
    data = {"patient_name": "John", "ssn": "123-45-6789", "safe_field": "ok"}
    result = redact(data)
    assert result["patient_name"] == "[REDACTED]"
    assert result["ssn"] == "[REDACTED]"
    assert result["safe_field"] == "ok"


def test_redacts_nested():
    data = {"outer": {"patient_id": "P123", "value": 42}}
    result = redact(data)
    assert result["outer"]["patient_id"] == "[REDACTED]"
    assert result["outer"]["value"] == 42


def test_redacts_in_lists():
    data = [{"api_key": "sk-123"}, {"name": "safe"}]
    result = redact(data)
    assert result[0]["api_key"] == "[REDACTED]"
    assert result[1]["name"] == "safe"


def test_redacts_various_patterns():
    data = {
        "mrn": "M001",
        "dob": "1990-01-01",
        "email_address": "a@b.com",
        "phone_number": "555-1234",
        "password": "hunter2",
        "api_key": "sk-test",
        "auth_token": "tok_abc",
        "secret_value": "shh",
        "credential_file": "/path",
        "authorization_header": "Bearer xyz",
    }
    result = redact(data)
    for key in data:
        assert result[key] == "[REDACTED]", f"{key} should be redacted"


def test_preserves_safe_fields():
    data = {"table_name": "encounters", "count": 42, "active": True, "items": [1, 2]}
    result = redact(data)
    assert result == data


def test_handles_none_and_primitives():
    assert redact(None) is None
    assert redact(42) == 42
    assert redact("hello") == "hello"
    assert redact(True) is True
