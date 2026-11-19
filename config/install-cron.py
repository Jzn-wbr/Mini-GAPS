"""Installation dans l'image uniquement : binaire épinglé et empreinte vérifiée."""
from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.request import urlopen

settings=json.loads(Path('/tmp/supercronic.json').read_text())
architecture=os.environ['TARGETARCH']
expected=settings['sha256'][architecture]
version=settings['version']
url=f'https://github.com/aptible/supercronic/releases/download/{version}/supercronic-linux-{architecture}'
with urlopen(url,timeout=60) as response:binary=response.read(20*1024*1024)
if sha256(binary).hexdigest()!=expected:raise SystemExit('Empreinte Supercronic incorrecte')
destination=Path('/usr/local/bin/supercronic');destination.write_bytes(binary);destination.chmod(0o755)
license_path=Path('/usr/share/doc/supercronic/LICENSE.md');license_path.parent.mkdir(parents=True,exist_ok=True)
with urlopen(f'https://raw.githubusercontent.com/aptible/supercronic/{version}/LICENSE.md',timeout=30) as response:
    license_path.write_bytes(response.read(32768))
