from app.cli import main


def test_text_output_stays_the_same():
    code, output = main(['1', '2', '3'])
    assert code == 0
    assert output == 'count=3 total=6 average=2.0'


def test_csv_output_is_available():
    code, output = main(['1', '2', '3', '--format', 'csv'])
    assert code == 0
    assert output == 'count,total,average\n3,6,2.0'
