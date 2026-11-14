import pytest
from pydantic import ValidationError

from cloze.core.models import DEFAULT_DENSITY, Policy, Token


def test_policy_defaults_follow_contract() -> None:
    policy = Policy(seed=1)
    assert policy.preset == "auto"
    assert policy.density == DEFAULT_DENSITY
    assert policy.language == "auto"
    assert policy.include_numbers is False


def test_policy_requires_seed_and_rejects_unknown_preset() -> None:
    with pytest.raises(ValidationError):
        Policy.model_validate({})
    with pytest.raises(ValidationError):
        Policy.model_validate({"seed": 1, "preset": "nope"})


def test_token_dumps_contract_field_names() -> None:
    token = Token(
        id=12,
        text="Mitochondria",
        bbox=(0.12, 0.31, 0.29, 0.35),
        line_id=4,
        font_size=18.0,
        fontname="Helvetica-Bold",
    )
    assert token.model_dump(mode="json") == {
        "id": 12,
        "text": "Mitochondria",
        "bbox": [0.12, 0.31, 0.29, 0.35],
        "word_bbox": None,
        "line_id": 4,
        "font_size": 18.0,
        "fontname": "Helvetica-Bold",
    }
