import pytest
from pydantic import ValidationError

from order_service import OrderCreate


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("user_id", 0),
        ("user_id", -1),
        ("item_name", ""),
        ("item_name", "x" * 201),
        ("amount", 0),
        ("amount", -0.01),
    ],
)
def test_order_schema_rejects_invalid_boundary_values(field, value):
    payload = {"user_id": 1, "item_name": "Valid item", "amount": 1.0}
    payload[field] = value

    with pytest.raises(ValidationError):
        OrderCreate.model_validate(payload)


def test_order_schema_accepts_boundary_values():
    order = OrderCreate(user_id=1, item_name="x" * 200, amount=0.01)

    assert order.user_id == 1
    assert len(order.item_name) == 200
    assert order.amount == 0.01
