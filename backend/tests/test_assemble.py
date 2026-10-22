"""Pages -> forms: continuation pages join the form before them.
Synthetic values only (iron rule 5)."""

from vera.models import FieldResult, Submission
from vera.pipeline.assemble import attach_continuations
from vera.pipeline.decide import FIELDS


def sub(page_id, kind="form", **values):
    fields = {
        f: FieldResult(
            field=f,
            values=values.get(f, []),
            status="likely" if values.get(f) else "missing",
            score=0.0,
            reasons=[],
            explanation_he="",
        )
        for f in FIELDS
    }
    return Submission(
        id=f"b:{page_id}",
        batch_id="b",
        page_ids=[page_id],
        profile_id="_generic",
        page_kind=kind,
        fields=fields,
        row_status="missing" if not values else "likely",
    )


def test_continuation_fills_only_what_its_form_is_missing():
    first = sub("doc/p01", central_numbers=["512340"], id_number=["012345674"])
    cont = sub("doc/p02", "continuation", central_numbers=["999999"],
               commitment_numbers=["A1234"])  # fmt: skip
    forms, joined = attach_continuations([first, cont])
    assert [f.id for f in forms] == ["b:doc/p01"] and joined == ["b:doc/p02"]
    form = forms[0]
    assert form.page_ids == ["doc/p01", "doc/p02"]
    assert form.fields["central_numbers"].values == ["512340"]  # first page wins
    assert form.fields["commitment_numbers"].values == ["A1234"]


def test_pages_that_are_forms_stay_forms():
    forms, joined = attach_continuations([sub("doc/p01"), sub("doc/p02")])
    assert len(forms) == 2 and not joined


def test_a_continuation_never_crosses_into_another_document():
    forms, joined = attach_continuations([sub("a/p01"), sub("b/p01", "continuation")])
    assert len(forms) == 2 and not joined


def test_a_page_read_again_is_not_added_twice():
    first = sub("doc/p01")
    first.page_ids = ["doc/p01", "doc/p02"]  # joined in an earlier run
    forms, joined = attach_continuations([first, sub("doc/p02", "continuation")])
    assert joined == ["b:doc/p02"]
    assert forms[0].page_ids == ["doc/p01", "doc/p02"]
