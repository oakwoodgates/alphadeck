"""Platform values (V2): a company's share count and cash/burn, one value per company for everyone.

- ``platform_values.state`` — the state read (W2a): the value the meters show and its state.
- ``platform_values.machine`` — the machine writer (W2b, not yet built).
- ``platform_values.steward`` — the steward's actions (W2c, not yet built).

The pure rules live in ``domain.platform_values``; the store in ``repositories.platform_value_repo``. Nothing on
the call path imports this package (``docs/PLATFORM_VALUES.md``).
"""
