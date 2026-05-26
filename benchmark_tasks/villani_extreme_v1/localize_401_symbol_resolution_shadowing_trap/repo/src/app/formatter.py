from .parser import parse_program
from .resolver import resolve_name

def format_resolution(lines, function_name, symbol):
    return f"{function_name}:{symbol} -> {resolve_name(parse_program(lines), function_name, symbol)}"
