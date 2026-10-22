"""Small read-only views of a Submission, shared by tubes, follow-ups,
reports and the assistant."""

from vera.models import Submission

# The five fields of the form signature ring, in drawing order.
SIGNATURE = [
    ("name", ("first_name", "last_name")),
    ("id_number", ("id_number",)),
    ("central_numbers", ("central_numbers",)),
    ("receipt_date", ("receipt_date",)),
    ("commitment_numbers", ("commitment_numbers",)),
]
ORDER = ("review", "missing", "likely", "human", "verified")


def number_key(text: str) -> str:
    """Digits/letters only, no leading zeros: how a tube label, a scanner
    and the form are compared."""
    return "".join(ch for ch in text.upper() if ch.isalnum()).lstrip("0")


def value(sub: Submission, field: str) -> str:
    fr = sub.fields.get(field)
    return ", ".join(fr.values) if fr else ""


def signature(sub: Submission) -> list[dict]:
    out = []
    for key, fields in SIGNATURE:
        statuses = [sub.fields[f].status for f in fields if f in sub.fields]
        st = min(statuses, key=ORDER.index) if statuses else "missing"
        out.append({"key": key, "status": "verified" if st == "human" else st})
    return out


def brief(sub: Submission) -> dict:
    return {
        "id": sub.id,
        "batch_id": sub.batch_id,
        "name": " ".join(
            v for v in (value(sub, "first_name"), value(sub, "last_name")) if v
        ),
        "sender": value(sub, "sender"),
        "central": value(sub, "central_numbers"),
        "commitment": value(sub, "commitment_numbers"),
        "row_status": sub.row_status,
        "signature": signature(sub),
    }
