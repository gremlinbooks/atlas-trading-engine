from __future__ import annotations

import json
from typing import Any, Dict, List

import requests

from app.config import get_settings


class OandaClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.oanda_api_key
        self.account_id = settings.oanda_account_id
        self.env = settings.oanda_env
        self.base_url = (
            "https://api-fxpractice.oanda.com"
            if self.env == "practice"
            else "https://api-fxtrade.oanda.com"
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def get_account_summary(self) -> Dict[str, Any]:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/summary"
        resp = requests.get(url, headers=self._headers(), timeout=20)
        resp.raise_for_status()
        return resp.json()

    def list_open_trades(self) -> Dict[str, Any]:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/openTrades"
        resp = requests.get(url, headers=self._headers(), timeout=20)
        resp.raise_for_status()
        return resp.json()

    def get_pricing(self, symbols: List[str]) -> Dict[str, Any]:
        instruments = ",".join(symbols)
        url = f"{self.base_url}/v3/accounts/{self.account_id}/pricing"
        resp = requests.get(
            url,
            headers=self._headers(),
            params={"instruments": instruments},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def place_market_order(self, symbol: str, units: float) -> Dict[str, Any]:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/orders"
        payload = {
            "order": {
                "type": "MARKET",
                "instrument": symbol,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
            }
        }
        resp = requests.post(
            url,
            headers=self._headers(),
            data=json.dumps(payload),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def close_trade(self, trade_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/v3/accounts/{self.account_id}/trades/{trade_id}/close"
        resp = requests.put(url, headers=self._headers(), timeout=20)
        resp.raise_for_status()
        return resp.json()

    def get_candles(self, symbol: str, granularity: str, count: int) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/v3/instruments/{symbol}/candles"
        resp = requests.get(
            url,
            headers=self._headers(),
            params={
                "price": "M",
                "granularity": granularity,
                "count": count,
            },
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        candles = payload.get("candles", [])
        normalized: List[Dict[str, Any]] = []
        for candle in candles:
            if not candle.get("complete", False):
                continue
            mid = candle.get("mid", {})
            normalized.append(
                {
                    "time": candle.get("time"),
                    "o": float(mid.get("o", 0)),
                    "h": float(mid.get("h", 0)),
                    "l": float(mid.get("l", 0)),
                    "c": float(mid.get("c", 0)),
                    "volume": int(candle.get("volume", 0)),
                }
            )
        return normalized

    def get_candles_range(
        self,
        *,
        symbol: str,
        granularity: str,
        from_ts: str,
        to_ts: str,
        count: int | None = 5000,
        include_first: bool = True,
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/v3/instruments/{symbol}/candles"
        params = {
            "price": "M",
            "granularity": granularity,
            "from": from_ts,
            "to": to_ts,
            "includeFirst": "true" if include_first else "false",
        }
        if not (from_ts and to_ts):
            if count is not None:
                params["count"] = count
        resp = requests.get(
            url,
            headers=self._headers(),
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        candles = payload.get("candles", [])
        normalized: List[Dict[str, Any]] = []
        for candle in candles:
            if not candle.get("complete", False):
                continue
            mid = candle.get("mid", {})
            normalized.append(
                {
                    "time": candle.get("time"),
                    "o": float(mid.get("o", 0)),
                    "h": float(mid.get("h", 0)),
                    "l": float(mid.get("l", 0)),
                    "c": float(mid.get("c", 0)),
                    "volume": int(candle.get("volume", 0)),
                }
            )
        return normalized
