from app.core import paginate

def test_final_page():
    assert paginate([1,2,3,4],3)[-1]==[4]
