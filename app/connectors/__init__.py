"""Connectors to external integration tools.

Each connector module registers itself with
:func:`~app.connectors.registry.register_connector` when imported.
Import every connector module below so it is available at runtime; a
type without a registered connector answers ``connector_not_available``.
"""
