"""Stage 9, pages -> forms (spec 7): a page the reader marked as a
continuation of the previous one joins that page's form instead of
becoming a form of its own.

decide_page decides one page at a time; this runs once the pages of a
batch are decided. A continuation page fills only the fields its form is
still missing: the first page is where the form's own fields are, and a
continuation (a list of tests, a second sheet) never overrides them. Every
value keeps its own page in its sources, so its evidence points to the
right page. A continuation with no page before it in its document stays a
form of its own.
"""

from vera.models import Submission, worst_status


def _doc(page_id: str) -> str:
    return page_id.rsplit("/", 1)[0]


def attach_continuations(subs: list[Submission]) -> tuple[list[Submission], list[str]]:
    """`subs` in batch order (document, then page). Returns the forms, and
    the ids of the continuation submissions that were joined into them."""
    forms: list[Submission] = []
    joined: list[str] = []
    for sub in subs:
        prev = forms[-1] if forms else None
        if (
            sub.page_kind == "continuation"
            and prev is not None
            and _doc(prev.page_ids[-1]) == _doc(sub.page_ids[0])
        ):
            joined.append(sub.id)
            if set(sub.page_ids) <= set(prev.page_ids):
                continue  # read again (a retry): it is already in its form
            prev.page_ids = prev.page_ids + sub.page_ids
            for name, fr in sub.fields.items():
                if not prev.fields[name].values and fr.values:
                    prev.fields[name] = fr
            prev.row_status = worst_status([f.status for f in prev.fields.values()])
            continue
        forms.append(sub)
    return forms, joined
