"""Read the employee's saved model; defaults are assigned only at creation."""
def resolve_employee_model(config: dict) -> str | None:
    return str(config.get('model') or '').strip() or None
