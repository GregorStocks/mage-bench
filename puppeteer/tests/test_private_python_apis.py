"""Weird test: ratchet cross-module private Python helper imports."""

from tests.test_weird_conventions import (
    _ALLOWED_PRIVATE_CROSS_MODULE_IMPORTS,
    _ALLOWED_PRIVATE_REEXPORTS,
    _private_cross_module_imports,
    _private_reexports,
)


class TestPrivatePythonApis:
    def test_no_new_cross_module_private_imports(self) -> None:
        unexpected = _private_cross_module_imports() - _ALLOWED_PRIVATE_CROSS_MODULE_IMPORTS
        assert not unexpected, (
            "New cross-module imports of underscore-prefixed helpers were added.\n"
            "If another module needs the helper, rename it to a public symbol in the owner module instead.\n  "
            + "\n  ".join(f"{importer} imports {exporter}.{name}" for importer, exporter, name in sorted(unexpected))
        )

    def test_no_new_private_reexports(self) -> None:
        unexpected = _private_reexports() - _ALLOWED_PRIVATE_REEXPORTS
        assert not unexpected, (
            "New underscore-prefixed names were added to __all__.\n"
            "Private helpers should not be part of a module's public export surface.\n  "
            + "\n  ".join(f"{module}.{name}" for module, name in sorted(unexpected))
        )
