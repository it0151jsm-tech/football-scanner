"""Read provider update evidence without manufacturing a market timestamp.

The Odds API v4 GET /sports/{sport}/odds documentation places ``last_update``
on the bookmaker in its response example; its markets need not repeat it:
https://the-odds-api.com/liveapi/guides/v4/#get-odds

A bookmaker timestamp is evidence of that bookmaker response's update, not a
guarantee that each market was individually refreshed at that instant. Prefer
an explicit market timestamp when present. Invalid explicit market evidence is
never concealed by falling back to the bookmaker or to local receipt time.
This helper does not authorize a quote: existing freshness/identity/price gates
still apply, including future-clock and stale-price rejection.
"""
from datetime import datetime

PROVIDER_DOCUMENTATION = 'https://the-odds-api.com/liveapi/guides/v4/#get-odds'


def _aware_timestamp(value):
    """Validate the actual JSON timestamp and retain its original representation."""
    if (not isinstance(value,str) or not value or value!=value.strip() or len(value)>64
            or any(ord(character)<32 for character in value)):
        return None
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return value if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (ValueError,TypeError,OverflowError):
        return None


def quote_update(bookmaker, market):
    """Return the real aware update instant and its provider scope.

    No network, receipt clock, mutation, cache stamping or forecast occurs here.
    The absence of a market field permits the documented bookmaker fallback;
    a present but invalid market field blocks fallback and remains unavailable.
    """
    if not isinstance(market,dict):
        return {'source_updated_at':None,'source_update_scope':'invalid_market'}
    if 'last_update' in market:
        timestamp=_aware_timestamp(market['last_update'])
        return {'source_updated_at':timestamp,
                'source_update_scope':'market' if timestamp is not None else 'invalid_market'}
    timestamp=_aware_timestamp(bookmaker.get('last_update')) if isinstance(bookmaker,dict) else None
    return {'source_updated_at':timestamp,
            'source_update_scope':'bookmaker' if timestamp is not None else 'missing'}
