"""Download the free DB-IP "IP to Country Lite" database (CC BY 4.0, https://db-ip.com) to geo/dbip-country-lite.mmdb.

Run at every image build (Dockerfile), so the live site always has this month's or last month's data. It is used only for the
developer's switch "also allow every address in the Netherlands" (app/ip_allowlist.py). Without the file that switch lets
nobody in by country (safe side), and the developer page says so.

    python scripts/fetch_geoip.py
"""
import gzip
import sys
import urllib.request
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / 'geo'


def months():
    today = date.today()
    yield today.strftime('%Y-%m')
    prev = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    yield prev.strftime('%Y-%m')


def main():
    OUT.mkdir(exist_ok=True)
    for month in months():
        url = f'https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'krein-build'}), timeout=60) as r:
                data = gzip.decompress(r.read())
        except Exception as exc:   # this month's file can appear a day late: try last month's
            print(f'{month}: {exc}', file=sys.stderr)
            continue
        if b'\xab\xcd\xefMaxMind.com' not in data[-131072:]:   # the metadata marker every .mmdb file ends with
            print(f'{month}: not a database file', file=sys.stderr)
            continue
        (OUT / 'dbip-country-lite.mmdb').write_bytes(data)
        (OUT / 'dbip-country-lite.txt').write_text(month, encoding='utf-8')
        print(f'country data {month}: {len(data) // 1024} KB')
        return 0
    print('country data not downloaded; the Netherlands switch will let nobody in by country', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
