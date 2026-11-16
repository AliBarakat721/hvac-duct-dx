"""Coefficient-driven HVAC fitting and dynamic pressure-loss API."""

from .engine import (
    FittingCalculationError,
    calculate_fitting_loss,
    calculate_fitting_losses,
    integrate_fitting_losses,
    validate_coefficient,
)
from .models import (
    CoefficientSource,
    CoefficientValidity,
    FittingCalculationStatus,
    FittingCoefficient,
    FittingGeometry,
    FittingInput,
    FittingIntegrationResult,
    FittingLossResult,
    FittingType,
    ReferenceVelocity,
)

__all__ = [
    "CoefficientSource",
    "CoefficientValidity",
    "FittingCalculationError",
    "FittingCalculationStatus",
    "FittingCoefficient",
    "FittingGeometry",
    "FittingInput",
    "FittingIntegrationResult",
    "FittingLossResult",
    "FittingType",
    "ReferenceVelocity",
    "calculate_fitting_loss",
    "calculate_fitting_losses",
    "integrate_fitting_losses",
    "validate_coefficient",
]
