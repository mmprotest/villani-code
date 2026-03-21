from .ingest import prepare_user_record
from .reporting import collect_report_labels


def preview_labels(values: list[str]) -> str:
    return ','.join(collect_report_labels([{'name': value} for value in values]))


def preview_records(values: list[str]) -> list[dict[str, str]]:
    return [prepare_user_record({'name': value}) for value in values]
