"""
Deterministic Subtask Expert Framework

Base class for creating experts with:
  - Strict input/output contracts (Pydantic)
  - Built-in deterministic validators
  - Ability boundary declarations

Usage:
    class MyExpert(DeterministicSubtaskExpert):
        ...
    expert = MyExpert()
    ok, result, boundary = expert.process(raw_dict)
"""

from abc import ABC, abstractmethod
from typing import Type, Tuple, Any
from pydantic import BaseModel, ValidationError


class DeterministicSubtaskExpert(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def version(self) -> str:
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Type[BaseModel]:
        pass

    @property
    @abstractmethod
    def output_schema(self) -> Type[BaseModel]:
        pass

    @abstractmethod
    def compute(self, input_data: BaseModel) -> BaseModel:
        pass

    @abstractmethod
    def ability_boundary(self) -> str:
        pass

    def validate_input(self, raw: dict) -> Tuple[bool, Any]:
        try:
            obj = self.input_schema(**raw)
            return True, obj
        except ValidationError as e:
            return False, str(e)

    def validate_output(self, output: BaseModel) -> Tuple[bool, str]:
        try:
            self.output_schema.model_validate(output)
            return True, ""
        except ValidationError as e:
            return False, str(e)

    def process(self, raw: dict) -> Tuple[bool, Any, str]:
        ok, inp_or_err = self.validate_input(raw)
        if not ok:
            return False, {"validation_error": inp_or_err}, self.ability_boundary()

        try:
            result = self.compute(inp_or_err)
        except (ValueError, ZeroDivisionError, OverflowError) as e:
            return False, {"compute_error": str(e)}, self.ability_boundary()

        ok, err = self.validate_output(result)
        if not ok:
            return False, {"output_validation_error": err}, self.ability_boundary()

        return True, result.model_dump(), self.ability_boundary()

    def info(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "input_schema": self.input_schema.model_json_schema(),
            "output_schema": self.output_schema.model_json_schema(),
            "ability_boundary": self.ability_boundary(),
        }
