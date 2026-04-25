from dataclasses import dataclass, field
@dataclass
class Assignment: target: str; source: str
@dataclass
class ImportAlias: alias: str; target: str
@dataclass
class FunctionDef: name: str; body: list = field(default_factory=list)
@dataclass
class Module: statements: list
