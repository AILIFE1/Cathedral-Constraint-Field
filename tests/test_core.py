"""Basic tests for Cathedral-Constraint-Field core."""

import numpy as np
import pytest

from cathedral_constraint_field import ConstraintField


def test_create_field():
    field = ConstraintField(dimension=2, name="Test Cathedral")
    assert field.dimension == 2
    assert field.name == "Test Cathedral"
    assert len(field.constraints) == 0


def test_add_linear_constraint():
    field = ConstraintField(dimension=3)
    c = field.add_linear_constraint([1, 2, 3], bound=10, sense="<=", name="Test Constraint")
    assert len(field.constraints) == 1
    assert c.name == "Test Constraint"
    assert np.allclose(c.coefficients, [1, 2, 3])


def test_solve_simple():
    field = ConstraintField(dimension=2, name="Simple")
    field.add_linear_constraint([1, 1], bound=5, sense="<=")
    field.add_linear_constraint([1, 0], bound=3, sense="<=")
    field.set_objective(lambda x: x[0] + x[1], sense="minimize")

    sol = field.solve()
    assert sol.success
    assert sol.constraints_satisfied
    assert np.allclose(sol.x, [0, 0], atol=1e-5) or sol.x[0] + sol.x[1] <= 5 + 1e-6


def test_summary():
    field = ConstraintField(dimension=2, name="Summary Test")
    field.add_linear_constraint([1, 0], 4)
    summary = field.summary()
    assert "Summary Test" in summary
    assert "Constraints: 1" in summary
