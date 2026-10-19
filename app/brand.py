"""The sender of every document."""
from pathlib import Path

# Every invoice, contract and statement is sent by Krein Property Management, whatever the landlord company is called:
# its name and logo head each document, and the landlord company is named as the party it acts for.
BRAND = 'Krein Property Management'
BRAND_LOGO = Path(__file__).resolve().parent.parent / 'static' / 'brand' / 'krein-logo.png'


def logo_bytes(company_id=None):
    return BRAND_LOGO.read_bytes() if BRAND_LOGO.exists() else None


def on_behalf(name):
    """The line under the Krein name: for which landlord the document is sent."""
    return f'Namens / On behalf of {name}' if name and name.strip().lower() != BRAND.lower() else ''
