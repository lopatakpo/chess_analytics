"""Sdílená konfigurace pytestu – přidá kořen aplikace na ``sys.path``, aby
testy v ``tests/`` mohly dělat ploché importy (``import accuracy`` apod.),
stejně jako to dělá samotná aplikace.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
