"""
Core module for Cathedral-Constraint-Field.

Provides the ConstraintField class — a structured way to define,
navigate, and solve systems of constraints with architectural intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import numpy as np
from scipy.optimize import linprog, minimize


@dataclass
class Constraint:
    """Represents a single constraint in the field."""
    name: str
    expression: str
    sense: Literal["<=", ">=", "=="]
    bound: float
    coefficients: np.ndarray | None = None
    constraint_type: str = "linear"


@dataclass
class Solution:
    """Result of solving a ConstraintField."""
    x: np.ndarray
    success: bool
    message: str
    objective_value: float | None = None
    constraints_satisfied: bool = False


class ConstraintField:
    """
    A Constraint Field — a deliberate architectural space of constraints.

    Think of it as designing a cathedral: every constraint is a pillar,
    arch, or flying buttress placed with purpose to create something
    greater than the sum of its parts.
    """

    def __init__(
        self,
        dimension: int,
        name: str = "Unnamed Cathedral",
        description: str | None = None,
    ):
        self.dimension = dimension
        self.name = name
        self.description = description or f"A {dimension}D constraint cathedral"
        self.constraints: list[Constraint] = []
        self.objective: Callable[[np.ndarray], float] | None = None
        self._bounds: list[tuple[float, float]] | None = None

    def add_linear_constraint(
        self,
        coefficients: list[float] | np.ndarray,
        bound: float,
        sense: Literal["<=", ">=", "=="] = "<=",
        name: str | None = None,
    ) -> Constraint:
        """Add a linear constraint of the form `coefficients · x  sense  bound`."""
        coeffs = np.asarray(coefficients, dtype=float)
        if len(coeffs) != self.dimension:
            raise ValueError(
                f"Coefficients must have length {self.dimension}, got {len(coeffs)}"
            )

        constraint = Constraint(
            name=name or f"LinearConstraint_{len(self.constraints) + 1}",
            expression=f"{coeffs} · x {sense} {bound}",
            sense=sense,
            bound=bound,
            coefficients=coeffs,
            constraint_type="linear",
        )
        self.constraints.append(constraint)
        return constraint

    def set_objective(
        self,
        objective: Callable[[np.ndarray], float],
        sense: Literal["minimize", "maximize"] = "minimize",
    ) -> None:
        """Set the objective function. Internally always minimizes."""
        if sense == "maximize":
            self.objective = lambda x: -objective(x)
        else:
            self.objective = objective

    def set_bounds(self, bounds: list[tuple[float, float]]) -> None:
        """Set box bounds for each dimension: [(low, high), ...]"""
        if len(bounds) != self.dimension:
            raise ValueError("Bounds must match dimension")
        self._bounds = bounds

    def solve(
        self,
        method: Literal["linprog", "slsqp", "auto"] = "auto",
        **solver_kwargs: Any,
    ) -> Solution:
        """
        Solve the current constraint field.

        Returns a Solution dataclass with the result.
        """
        if not self.constraints:
            # Trivial case
            x0 = np.zeros(self.dimension)
            return Solution(
                x=x0,
                success=True,
                message="No constraints — trivial solution at origin",
                objective_value=self.objective(x0) if self.objective else None,
            )

        # Separate equality and inequality for linprog
        A_ub, b_ub = [], []
        A_eq, b_eq = [], []

        for c in self.constraints:
            if c.coefficients is None:
                continue
            if c.sense == "<=":
                A_ub.append(c.coefficients)
                b_ub.append(c.bound)
            elif c.sense == ">=":
                A_ub.append(-c.coefficients)
                b_ub.append(-c.bound)
            elif c.sense == "==":
                A_eq.append(c.coefficients)
                b_eq.append(c.bound)

        bounds = self._bounds or [(None, None)] * self.dimension

        if method in ("linprog", "auto") and A_ub:
            # Use linear programming when possible
            c = np.zeros(self.dimension)  # feasibility problem
            if self.objective:
                # Approximate linear objective if possible (for demo)
                c = np.ones(self.dimension)  # placeholder

            res = linprog(
                c,
                A_ub=np.array(A_ub) if A_ub else None,
                b_ub=np.array(b_ub) if b_ub else None,
                A_eq=np.array(A_eq) if A_eq else None,
                b_eq=np.array(b_eq) if b_eq else None,
                bounds=bounds,
                method="highs",
                **solver_kwargs,
            )

            if res.success:
                obj_val = self.objective(res.x) if self.objective else None
                return Solution(
                    x=res.x,
                    success=True,
                    message=res.message,
                    objective_value=obj_val,
                    constraints_satisfied=True,
                )

        # Fallback to SLSQP for nonlinear / more complex cases
        if self.objective is None:
            def default_obj(x: np.ndarray) -> float:
                return np.sum(x**2)  # minimize distance from origin
            obj = default_obj
        else:
            obj = self.objective

        # Build constraint dicts for minimize
        cons = []
        for c in self.constraints:
            if c.coefficients is None:
                continue
            if c.sense == "<=":
                cons.append({
                    "type": "ineq",
                    "fun": lambda x, coeffs=c.coefficients, b=c.bound: b - np.dot(coeffs, x)
                })
            elif c.sense == ">=":
                cons.append({
                    "type": "ineq",
                    "fun": lambda x, coeffs=c.coefficients, b=c.bound: np.dot(coeffs, x) - b
                })
            elif c.sense == "==":
                cons.append({
                    "type": "eq",
                    "fun": lambda x, coeffs=c.coefficients, b=c.bound: np.dot(coeffs, x) - b
                })

        x0 = np.zeros(self.dimension)
        res = minimize(
            obj,
            x0,
            bounds=bounds,
            constraints=cons,
            method="SLSQP",
            **solver_kwargs,
        )

        return Solution(
            x=res.x,
            success=res.success,
            message=res.message,
            objective_value=res.fun if res.success else None,
            constraints_satisfied=res.success,
        )

    def visualize(self, resolution: int = 100) -> None:
        """Simple 2D visualization if dimension == 2 (placeholder)."""
        if self.dimension != 2:
            print(f"Visualization currently only supported for 2D fields (got {self.dimension}D)")
            return

        import matplotlib.pyplot as plt

        # Very basic feasible region plot (expand later)
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.set_title(f"{self.name} — Constraint Field")
        ax.set_xlabel("x₁")
        ax.set_ylabel("x₂")
        ax.grid(True, alpha=0.3)

        # Placeholder: show constraint lines
        x = np.linspace(-10, 10, resolution)
        for c in self.constraints:
            if c.coefficients is not None and len(c.coefficients) == 2:
                a, b = c.coefficients
                if abs(b) > 1e-8:
                    y = (c.bound - a * x) / b
                    label = c.name
                    ax.plot(x, y, label=label, linewidth=2)

        ax.legend()
        plt.tight_layout()
        plt.show()

    def summary(self) -> str:
        """Human-readable summary of the current field."""
        lines = [
            f"Cathedral: {self.name}",
            f"Dimensions: {self.dimension}",
            f"Constraints: {len(self.constraints)}",
        ]
        for c in self.constraints:
            lines.append(f"  • {c.name}: {c.expression}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"ConstraintField(name={self.name!r}, dim={self.dimension}, constraints={len(self.constraints)})"
