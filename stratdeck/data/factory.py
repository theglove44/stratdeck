# stratdeck/data/factory.py
import os
from typing import Optional

from .provider import IDataProvider
from .mock_provider import MockProvider
from .tasty_provider import TastyProvider

_provider_instance: Optional[IDataProvider] = None

def get_provider() -> IDataProvider:
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance
    mode = os.getenv("STRATDECK_DATA_MODE", "mock").lower()
    if mode == "live":
        _provider_instance = TastyProvider()
    else:
        _provider_instance = MockProvider()
    return _provider_instance
