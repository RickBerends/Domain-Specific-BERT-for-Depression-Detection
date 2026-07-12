"""Shared contract package.

The *only* surface shared between the ``ingest`` and ``chat`` subsystems
(technical plan §2, §8). Both sides import these models; neither imports the
other. Any ingest source — a dataset loader, the fixture shop, or the eventual
van Bilsen crawler — is legitimate as long as it emits these types.
"""

from schemas import snapshot_format
from schemas.models import (
    ColorType,
    Content,
    Language,
    PriceTier,
    Product,
    ProductCard,
    SnapshotRef,
    StockStatus,
)

__all__ = [
    "ColorType",
    "Content",
    "Language",
    "PriceTier",
    "Product",
    "ProductCard",
    "SnapshotRef",
    "StockStatus",
    "snapshot_format",
]
