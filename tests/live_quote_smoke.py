import time
from datetime import datetime, timezone

from stratdeck.data.live_quotes import (
    LiveMarketDataService,
    make_tasty_streaming_session_from_env,
)
from stratdeck.data.tasty_provider import TastyProvider


def main() -> None:
    session = make_tasty_streaming_session_from_env()
    if session is None:
        raise SystemExit(
            "DXLink smoke test requires TASTY_CLIENT_SECRET and TASTY_REFRESH_TOKEN"
        )

    live = LiveMarketDataService(session=session, symbols=["SPX", "XSP"])

    with live:
        # REST provider, but backed by streaming cache
        provider = TastyProvider(live_quotes=live)

        for i in range(10):
            q = provider.get_quote("SPX")
            now = datetime.now(timezone.utc).isoformat()
            print(f"{now} SPX mid={q.get('mid')} source={q.get('source')}")
            time.sleep(1)


if __name__ == "__main__":
    main()
