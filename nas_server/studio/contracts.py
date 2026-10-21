"""Predeclared operation-specific measurement contracts, never maker waivers."""
from dataclasses import dataclass
import math
from typing import Literal
from .measure import MeasurementRunner, Receipt, FUNCTIONS


@dataclass(frozen=True)
class Requirement:
    name: str
    phase: Literal['pre', 'post']
    function: str
    category: Literal['hard_gate', 'diagnostic', 'taste']
    bindings: tuple[tuple[str, str], ...]  # receipt role -> step input role or '$output'
    mask: str | None = None  # step mask role
    roi: str | None = None  # step ROI role
    field: str | None = None
    settings: tuple[tuple[str, float], ...] = ()  # frozen measurement thresholds
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self):
        if (not self.name or self.phase not in ('pre', 'post') or self.function not in FUNCTIONS
                or self.category not in ('hard_gate', 'diagnostic', 'taste') or not self.bindings):
            raise ValueError('invalid requirement')
        if len(dict(self.bindings)) != len(self.bindings):
            raise ValueError('duplicate input binding')
        if self.function == 'minor_axis' and self.category == 'hard_gate':
            raise ValueError('minor-axis symmetry is diagnostic, never a universal hard gate')
        if self.category == 'hard_gate' and (self.field is None or
                (self.minimum is None and self.maximum is None)):
            raise ValueError('hard gates need a numeric field and predeclared bounds')
        for bound in (self.minimum, self.maximum):
            if bound is not None and (isinstance(bound, bool) or not math.isfinite(bound)):
                raise ValueError('finite numeric bounds required')
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError('inverted bounds')


@dataclass(frozen=True)
class Contract:
    op: str
    version: str
    requirements: tuple[Requirement, ...]

    def __post_init__(self):
        if not self.op or not self.version or not self.requirements:
            raise ValueError('contract requires an operation, version and measurements')
        keys = [(r.phase, r.name) for r in self.requirements]
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate requirement')
        if not {'pre', 'post'}.issubset({r.phase for r in self.requirements}):
            raise ValueError('contract must declare pre and post measurements')


def validate_step(step, contract: Contract, runner: MeasurementRunner) -> list[str]:
    """Return every failure; a failed/reverted attempt remains valid audit history.

    Callers may log invalid trials; Ledger refuses a kept step with any failure.
    Contract objects must come from the independent frozen contract registry.
    A maker choosing a different contract is outside this package's trust boundary.
    """
    failures = []
    if step.op != contract.op or step.contract_version != contract.version:
        failures.append('operation/metric-contract version mismatch')
    for req in contract.requirements:
        receipts = step.pre if req.phase == 'pre' else step.post
        receipt = receipts.get(req.name)
        if receipt is None:
            failures.append(f'{req.phase}.{req.name}: missing required measurement')
            continue
        try:
            runner.verify(receipt)
            if receipt.function != req.function:
                raise ValueError('wrong measurement function')
            expected = {}
            for receipt_role, step_role in req.bindings:
                expected[receipt_role] = (step.output_hash if step_role == '$output'
                                         else step.inputs[step_role])
            if dict(receipt.inputs) != expected:
                raise ValueError('stale or different input/output hash')
            expected_mask = step.masks[req.mask] if req.mask else None
            expected_roi = step.rois[req.roi] if req.roi else None
            if receipt.mask_hash != expected_mask or receipt.roi_hash != expected_roi:
                raise ValueError('wrong mask/ROI hash')
            for setting, expected_value in req.settings:
                if receipt.value.get(setting) != expected_value:
                    raise ValueError('measurement settings differ from frozen contract')
            if req.field is not None:
                value = receipt.value[req.field]
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                    raise ValueError('numeric metric required; prose cannot supply it')
                outside = ((req.minimum is not None and value < req.minimum)
                           or (req.maximum is not None and value > req.maximum))
                if outside and req.category == 'hard_gate':
                    raise ValueError('hard gate failed; no waiver is permitted')
        except (ValueError, KeyError, TypeError) as e:
            failures.append(f'{req.phase}.{req.name}: {e}')
    for phase, receipts in [('pre', step.pre), ('post', step.post)]:
        allowed = {r.name for r in contract.requirements if r.phase == phase}
        if set(receipts) - allowed:
            failures.append(f'{phase}: undeclared measurements')
        for name, receipt in receipts.items():
            try:
                runner.verify(receipt)
            except (ValueError, TypeError) as e:
                failures.append(f'{phase}.{name}: {e}')
    return failures


def intensity_contract(op: str) -> Contract:
    """Small seed contracts; coverage/flux invariance is deliberately not universal.

    Crop and star reduction may change coverage and stellar flux. The seed
    protects only bounded display clipping, on explicitly distinct pre/post
    populations. Physical/linear operations need their own frozen contracts.
    """
    if op not in ('crop', 'star_reduction', 'display_stretch'):
        raise ValueError('no seed contract for this operation')
    return Contract(op, 'display-clipping-1', (
        Requirement('clipping', 'pre', 'clipping', 'diagnostic', (('image', 'image'),), mask='pre', settings=(('black', 0.), ('white', 1.), ('near_black', .001))),
        Requirement('clipping', 'post', 'clipping', 'hard_gate', (('image', '$output'),),
                    mask='post', field='high_fraction', maximum=.01,
                    settings=(('black', 0.), ('white', 1.), ('near_black', .001))),
    ))
