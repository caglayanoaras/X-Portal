"""Atlas: an object database whose classes are defined at runtime.

db.py           connections to atlas.db, a SQLite file separate from the portal's
schema.py       class definitions and the tables behind them
values.py       validating and converting attribute values
permissions.py  who may create, read, update and delete what
objects.py      listing, reading and writing objects
errors.py       the error type the API returns as JSON
"""
