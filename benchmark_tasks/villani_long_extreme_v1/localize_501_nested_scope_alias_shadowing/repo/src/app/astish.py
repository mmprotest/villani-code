from dataclasses import dataclass, field
@dataclass
class Ref: name:str
@dataclass
class Assign: name:str; value:object
@dataclass
class Scope: alias_bindings:dict=field(default_factory=dict); value_bindings:dict=field(default_factory=dict)
