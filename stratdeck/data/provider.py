# stratdeck/data/provider.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class IDataProvider(ABC):
    @abstractmethod
    def get_quote(self, symbol: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> Dict[str, Any]:
        ...

    @abstractmethod
    def get_account_summary(self) -> Dict[str, Any]:
        """Return keys like: {'buying_power': float, 'cash': float, 'equity': float}"""
        ...

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """List of current positions with keys you use downstream."""
        ...

    @abstractmethod
    def preview_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        ...

    @abstractmethod
    def place_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        ...