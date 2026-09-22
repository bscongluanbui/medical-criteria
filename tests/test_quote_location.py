import pytest
from app.research import locate_quote
from app.schemas import Card
from tests.fixtures import card


def evidence():
    e=Card.model_validate(card()).evidence[0]
    e.quote='SYNTHETIC source passage with value >= 5.0 mm'
    e.pdf_page=1
    e.table=e.table_cell=e.footnote=e.bbox=None
    return e


def test_exact_unique_page_can_be_relocated():
    e=evidence();e.printed_page='old-label'
    ok,detail=locate_quote(e,[{'text':'cover'},{'text':e.quote}])
    assert ok and e.pdf_page==2 and e.printed_page is None
    assert detail['cited_pdf_page']==1 and detail['matching_pdf_pages']==[2]

@pytest.mark.parametrize('replacement',['SYNTHETIC source passage with value > 5.0 mm','SYNTHETIC source passage with value >= 6.0 mm','A paraphrase'])
def test_no_fuzzy_numeric_or_paraphrase_match(replacement):
    e=evidence()
    ok,detail=locate_quote(e,[{'text':replacement}])
    assert not ok and detail['code']=='QUOTE_NOT_IN_PARSED_DOCUMENT'
    assert e.pdf_page==1


def test_repeated_quote_is_ambiguous():
    e=evidence()
    ok,detail=locate_quote(e,[{'text':'cover'},{'text':e.quote},{'text':e.quote}])
    assert not ok and detail['code']=='QUOTE_PAGE_AMBIGUOUS'


def test_table_location_not_silently_moved():
    e=evidence();e.table='Table 1'
    ok,detail=locate_quote(e,[{'text':'cover'},{'text':e.quote}])
    assert not ok and detail['code']=='STRUCTURED_LOCATOR_REQUIRES_REVIEW'


def test_whitespace_only_is_allowed():
    e=evidence()
    assert locate_quote(e,[{'text':e.quote.replace(' ','\n  ')}])==(True,None)


def test_too_short_is_explicit():
    e=evidence();e.quote='tiny'
    assert locate_quote(e,[{'text':'tiny'}])[1]['code']=='QUOTE_TOO_SHORT'
