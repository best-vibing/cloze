import pytest
from pydantic import ValidationError

from cloze.core.models import DEFAULT_DENSITY, Blank, Policy, Token


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


def _contract_blank() -> dict[str, object]:
    # docs/design/data-contract.md 1.4 예시의 blanks 항목에 order만 더했다.
    return {
        "token_ids": [12],
        "answer": "Mitochondria",
        "bbox": [0.115, 0.305, 0.295, 0.355],
        "type": "term",
        "hint": "세포 소기관",
        "variants": [],
        "answer_group": None,
        "style": {"font_size_ratio": 0.025, "font_color": "#1a1a1a", "bg_color": "#ffffff"},
        "order": 1,
    }


def test_blank_accepts_contract_example() -> None:
    data = _contract_blank()
    assert Blank.model_validate(data).model_dump(mode="json") == data


def test_blank_rejects_empty_token_ids() -> None:
    with pytest.raises(ValidationError):
        Blank.model_validate({**_contract_blank(), "token_ids": []})


def test_blank_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        Blank.model_validate({**_contract_blank(), "type": "phrase"})
