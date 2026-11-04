# conftest.py — macht die Tests auch ohne brother_ql lauffaehig.
#
# app.py importiert brother_ql auf Modulebene (Druck-Backend). Auf einem Entwicklungsrechner
# ohne brother_ql (bzw. dessen schwer installierbarem Dependency-Pin, siehe Karte 1655)
# brauchen die Tests trotzdem die Rendering-Logik — der Stub ersetzt nur das Druck-Backend,
# nie das Label-Rendering. Wo brother_ql installiert ist (CI-Image, Pi), greift der Stub
# nicht.
try:
    import brother_ql  # noqa: F401
except ImportError:
    import sys
    import types

    # Vollstaendiger Modulbaum MIT Parent-Attributen — sonst scheitert
    # mock.patch("brother_ql.backends.helpers.send") an getattr(brother_ql, "backends").
    bql = types.ModuleType("brother_ql")
    conv = types.ModuleType("brother_ql.conversion")
    bck = types.ModuleType("brother_ql.backends")
    hlp = types.ModuleType("brother_ql.backends.helpers")
    rst = types.ModuleType("brother_ql.raster")
    bql.conversion = conv
    bql.backends = bck
    bql.raster = rst
    bck.helpers = hlp

    conv.convert = lambda *a, **k: None
    hlp.send = lambda *a, **k: None

    class _RasterStub:
        def __init__(self, *a, **k):
            self.exception_on_warning = True
            self.data = b""
    rst.BrotherQLRaster = _RasterStub

    sys.modules.update({
        "brother_ql": bql,
        "brother_ql.conversion": conv,
        "brother_ql.backends": bck,
        "brother_ql.backends.helpers": hlp,
        "brother_ql.raster": rst,
    })

import pytest


@pytest.fixture()
def kiosk():
    """Das app-Modul frisch geladen — so wie die Bestandstests es pro Test selbst machen."""
    import importlib
    import simulator
    import app as app_module
    importlib.reload(simulator)
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    return app_module
