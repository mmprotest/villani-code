from app.paths import normalize_path


def test_windows_backslashes_become_forward_slashes():
    assert normalize_path("C:\\Temp\\data\\file.txt") == "C:/Temp/data/file.txt"


def test_preserve_drive_letter_case_and_root():
    assert normalize_path("C:\\") == "C:/"


def test_redundant_separators_are_collapsed():
    assert normalize_path("logs//2026///03") == "logs/2026/03"
