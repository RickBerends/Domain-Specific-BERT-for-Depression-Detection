"""Publish gates + diff report (roadmap 3.4, technical plan §4.4).

A crawl only becomes a published snapshot if it passes these gates — the safety
net that catches broken selectors, price glitches, and stale offers before they
ever reach a customer. Everything is thresholded and reported, so a failed run
explains itself instead of silently shipping bad data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from schemas import Product


@dataclass
class Thresholds:
    max_shrink_pct: float = 30.0        # vs previous published product count
    min_price_present_pct: float = 90.0  # products that must carry a price
    max_dead_link_pct: float = 5.0
    price_cap_cents: int = 100_000       # €1000 sanity ceiling
    max_expired_offers: int = 0          # active offers already past their date


@dataclass
class Diff:
    new: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    diff: Diff = field(default_factory=Diff)

    def to_markdown(self) -> str:
        status = "PASS ✅" if self.passed else "FAIL ❌"
        lines = [f"# Crawl validation — {status}", "", "## Stats"]
        lines += [f"- {k}: {v}" for k, v in self.stats.items()]
        if self.failures:
            lines += ["", "## Failures"] + [f"- {f}" for f in self.failures]
        if self.warnings:
            lines += ["", "## Warnings"] + [f"- {w}" for w in self.warnings]
        lines += [
            "", "## Diff vs previous",
            f"- new: {len(self.diff.new)}",
            f"- removed: {len(self.diff.removed)}",
            f"- kept: {len(self.diff.kept)}",
        ]
        return "\n".join(lines) + "\n"


def diff_products(previous_slugs: set[str], products: list[Product]) -> Diff:
    current = {p.slug for p in products}
    return Diff(
        new=sorted(current - previous_slugs),
        removed=sorted(previous_slugs - current),
        kept=sorted(current & previous_slugs),
    )


def validate(
    products: list[Product],
    crawl_stats,
    *,
    previous_count: int | None = None,
    previous_slugs: set[str] | None = None,
    parse_errors: int = 0,
    thresholds: Thresholds | None = None,
    now: datetime | None = None,
) -> ValidationReport:
    t = thresholds or Thresholds()
    now = now or datetime.now(timezone.utc)
    failures: list[str] = []
    warnings: list[str] = []

    n = len(products)
    with_price = sum(1 for p in products if p.price_cents is not None)
    price_present_pct = 100.0 * with_price / n if n else 0.0

    fetched = getattr(crawl_stats, "fetched", 0)
    dead = getattr(crawl_stats, "dead_links", 0)
    dead_pct = 100.0 * dead / max(1, fetched + dead)

    over_cap = [p.slug for p in products if p.price_cents and p.price_cents > t.price_cap_cents]
    expired = [
        p.slug for p in products
        if p.offer_valid_until and _aware(p.offer_valid_until) < now
    ]

    # --- gates ---
    if n == 0:
        failures.append("no products parsed")
    if previous_count:
        shrink = 100.0 * (previous_count - n) / previous_count
        if shrink > t.max_shrink_pct:
            failures.append(
                f"catalogue shrank {shrink:.0f}% ({previous_count}→{n}), "
                f"limit {t.max_shrink_pct:.0f}% — likely broken selectors")
    if n and price_present_pct < t.min_price_present_pct:
        failures.append(
            f"only {price_present_pct:.0f}% of products have a price "
            f"(min {t.min_price_present_pct:.0f}%)")
    if dead_pct > t.max_dead_link_pct:
        failures.append(f"dead-link rate {dead_pct:.1f}% exceeds {t.max_dead_link_pct:.0f}%")
    if over_cap:
        failures.append(f"{len(over_cap)} price(s) over €{t.price_cap_cents//100} sanity cap: {over_cap[:3]}")
    if len(expired) > t.max_expired_offers:
        failures.append(f"{len(expired)} product(s) advertise an already-expired offer: {expired[:3]}")

    if parse_errors:
        warnings.append(f"{parse_errors} page(s) failed to parse (quarantined)")

    diff = diff_products(previous_slugs or set(), products)
    stats = {
        "products": n,
        "with_price_pct": round(price_present_pct, 1),
        "pages_fetched": fetched,
        "dead_links": dead,
        "dead_link_pct": round(dead_pct, 1),
        "parse_errors": parse_errors,
        "over_price_cap": len(over_cap),
        "expired_offers": len(expired),
    }
    return ValidationReport(
        passed=not failures, failures=failures, warnings=warnings, stats=stats, diff=diff
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
