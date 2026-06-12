#!/usr/bin/env python
"""
Simple example of building and solving a ConstraintField.

Run with: python examples/simple_cathedral.py
"""

from cathedral_constraint_field import ConstraintField
import numpy as np

print("🏛o  Building a small Cathedral Constraint Field...\n")

# Create a 2D cathedral
cathedral = ConstraintField(
    dimension=2,
    name="Harmony Cathedral",
    description="A simple 2D field demonstrating resource and balance constraints."
)

# Add some meaningful constraints
cathedral.add_linear_constraint(
    coefficients=[1, 0],
    bound=4,
    sense="<=",
    name="Resource A Limit"
)

cathedral.add_linear_constraint(
    coefficients=[0, 1],
    bound=3,
    sense="<=",
    name="Resource B Limit"
)

cathedral.add_linear_constraint(
    coefficients=[1, 1],
    bound=5,
    sense="<=",
    name="Combined Budget"
)

# Set a nice objective: maximize "harmony" = minimize negative harmony
def harmony(x: np.ndarray) -> float:
    return -(x[0] * 2 + x[1] * 1.5)  # weighted preference

cathedral.set_objective(harmony, sense="maximize")

print(cathedral.summary())
print("\nSolving the field...\n")

solution = cathedral.solve()

print("Solution found:")
print(f"  x = {solution.x}")
print(f"  Success: {solution.success}")
print(f"  Objective value: {solution.objective_value}")
print(f"  All constraints satisfied: {solution.constraints_satisfied}")

print("\nVisualizing (if matplotlib available)...")
try:
    cathedral.visualize()
except Exception as e:
    print(f"Visualization skipped: {e}")

print("\n✅ Cathedral constructed and solved successfully.")
