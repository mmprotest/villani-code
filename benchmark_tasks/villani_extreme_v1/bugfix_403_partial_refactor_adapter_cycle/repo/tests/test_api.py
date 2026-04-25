from app.core.v2 import NormalizedUser
from app.legacy.v1 import LegacyUser
from app.public_api import greet_user, serialize_legacy_payload

def test_modern_callers_still_work(): assert greet_user(NormalizedUser("u1","Ada")) == "hello Ada"

def test_legacy_payload_shape_is_preserved(): assert serialize_legacy_payload(LegacyUser("u1","Ada")) == {"uid":"u1","name":"Ada"}

def test_v2_input_can_still_roundtrip_through_legacy_serializer(): assert serialize_legacy_payload(NormalizedUser("u2","Grace")) == {"uid":"u2","name":"Grace"}
