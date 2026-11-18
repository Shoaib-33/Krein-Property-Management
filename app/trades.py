"""The trades of the suppliers: who performs a work order (electrician, plumber, carpenter, ...).

A supplier can have several trades, stored as one text ("Electrician, Plumber"). A work order and a recurring
maintenance plan name the trade that is needed, so the supplier list can be narrowed to the companies of that trade.
The same list is in static/app.js (TRADES)."""

TRADES = ('Electrician', 'Plumber', 'Carpenter', 'Heating engineer', 'Painter', 'Roofer', 'Glazier', 'Locksmith',
          'Tiler', 'Plasterer', 'Bricklayer', 'Ventilation engineer', 'Fire safety', 'Gardener', 'Cleaner',
          'Pest control', 'Handyman', 'Removals')


def trade(data, key='trade'):
    """The trade a work order needs: one of TRADES, or '' when it does not matter."""
    value = str(data.get(key) or '').strip()
    return value if value in TRADES else ''
