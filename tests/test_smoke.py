import pokesim

def test_version_exists():
    assert hasattr(pokesim, "__version__")
    assert isinstance(pokesim.__version__, str)

def test_version_format():
    parts = pokesim.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
