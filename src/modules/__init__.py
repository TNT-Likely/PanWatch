"""Business modules for the PanWatch modular monolith.

Each child owns one capability. Cross-module integration uses an owning
module's public service, DTO, or event—not its ORM models or repository.
"""
