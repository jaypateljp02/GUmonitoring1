import httpx, re

r = httpx.get('https://gu-monitoring.initiativesewafoundation.com/')
html = r.text

scripts = re.findall(r'src=["\']([^"\']+)["\']', html)
css_links = re.findall(r'href=["\']([^"\']+)["\']', html)

print('=== CHECKING EXTERNAL SCRIPTS ===')
for src in scripts:
    if src.endswith('.js') or 'cdn' in src:
        url = src if src.startswith('http') else 'https://gu-monitoring.initiativesewafoundation.com' + src
        try:
            res = httpx.get(url, timeout=5.0)
            print(f'Script {src} -> Status {res.status_code} ({len(res.content)} bytes)')
        except Exception as e:
            print(f'Script {src} -> FAILED: {e}')

print('\n=== CHECKING CSS LINKS ===')
for href in css_links:
    if href.endswith('.css') or 'fonts' in href or 'cdn' in href:
        url = href if href.startswith('http') else 'https://gu-monitoring.initiativesewafoundation.com' + href
        try:
            res = httpx.get(url, timeout=5.0)
            print(f'CSS {href} -> Status {res.status_code} ({len(res.content)} bytes)')
        except Exception as e:
            print(f'CSS {href} -> FAILED: {e}')
