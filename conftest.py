"""Sdílená konfigurace pytestu – přidá kořen aplikace na ``sys.path``, aby
testy v ``tests/`` mohly dělat ploché importy (``import accuracy`` apod.),
stejně jako to dělá samotná aplikace.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# GUI testy (MainWindow) běží bez displeje
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
