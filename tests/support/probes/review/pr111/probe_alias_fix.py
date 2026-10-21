"""Probe: does model_validate(by_alias=True, by_name=True) restore aliased fields, nested included?"""
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Inner(BaseModel):
    qty: int = Field(default=0, alias="Qty")


class Agg(BaseModel):
    total: int = Field(default=0, alias="totalQty")
    label: str = Field(alias="Label")
    alt: int = Field(default=0, validation_alias=AliasChoices("a1", "a2"))
    inner: Inner = Field(default_factory=Inner)


class SerByAlias(Agg):
    model_config = ConfigDict(serialize_by_alias=True)


a = Agg(totalQty=3, Label="L", a1=4, inner=Inner(Qty=5))
dumped = a.model_dump()
print("dump keys:", dumped)
try:
    print("plain validate :", Agg.model_validate(dumped).model_dump())
except Exception as e:
    print("plain validate : RAISED", type(e).__name__)
print("by_name fix    :", Agg.model_validate(dumped, by_alias=True, by_name=True).model_dump())
b = SerByAlias(totalQty=3, Label="L", a1=4, inner=Inner(Qty=5))
d2 = b.model_dump()
print("serialize_by_alias dump:", d2)
print("by_name fix on it:", SerByAlias.model_validate(d2, by_alias=True, by_name=True).model_dump(by_alias=False))
