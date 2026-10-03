"""
Identity search / de-duplication: looks for existing assessments that
plausibly describe the same request, before a new one is created, so
intake doesn't silently create duplicate records for the same
product/entity/owner. Deliberately a fast, transparent heuristic (case
-insensitive exact/substring match on a few identity fields) rather than
fuzzy matching, since these are free-text business fields, not a
structured identity database.
"""

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.assessment import Assessment


def find_potential_duplicates(
    db: Session,
    product_or_service_name: str | None,
    legal_entity: str | None,
    business_owner: str | None,
    exclude_id: int | None = None,
    limit: int = 10,
) -> list[Assessment]:
    """
    Returns existing assessments that match on:
      - product_or_service_name AND legal_entity (same offering, same
        entity), OR
      - product_or_service_name AND business_owner (same offering, same
        owner)
    Case-insensitive exact match on the trimmed values. Blank identity
    fields never match (an empty product name shouldn't flag every other
    draft with a blank product name as a "duplicate").
    """

    product = (product_or_service_name or "").strip()

    if not product:
        return []

    query = db.query(Assessment).filter(
        func.lower(Assessment.product_or_service_name) == product.lower()
    )

    if exclude_id is not None:
        query = query.filter(Assessment.id != exclude_id)

    entity = (legal_entity or "").strip()
    owner = (business_owner or "").strip()

    identity_matches = []

    if entity:
        identity_matches.append(
            func.lower(Assessment.legal_entity) == entity.lower()
        )

    if owner:
        identity_matches.append(
            func.lower(Assessment.business_owner) == owner.lower()
        )

    if not identity_matches:
        # Nothing besides the product name to narrow on — still surface
        # same-product matches, since that alone is a useful signal.
        return query.order_by(Assessment.created_at.desc()).limit(limit).all()

    query = query.filter(or_(*identity_matches))

    return query.order_by(Assessment.created_at.desc()).limit(limit).all()
