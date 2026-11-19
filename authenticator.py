"""Make admin QR codes for an authenticator app (Google Authenticator, Authy...).

    python authenticator.py            QR codes for every ADMIN_TOTP_* in .env
    python authenticator.py Maya       also creates a new secret for Maya and adds it to .env

QR images go to instance/admin_qr/ (git-ignored). Share each one only with that admin.
"""
import os
import sys

import pyotp
import qrcode
from dotenv import load_dotenv

load_dotenv('.env')
for name in sys.argv[1:]:
    if not os.environ.get(f'ADMIN_TOTP_{name}'):
        secret = pyotp.random_base32()
        with open('.env', 'a', encoding='utf-8') as f:
            f.write(f'\nADMIN_TOTP_{name}={secret}\n')
        os.environ[f'ADMIN_TOTP_{name}'] = secret

out = os.path.join('instance', 'admin_qr')
os.makedirs(out, exist_ok=True)
for key, secret in os.environ.items():
    if key.upper().startswith('ADMIN_TOTP_') and secret:
        name = key[len('ADMIN_TOTP_'):].title()
        uri = pyotp.TOTP(secret).provisioning_uri(name=name, issuer_name="Neighborhood Watch")
        path = os.path.join(out, f'{name}.png')
        qrcode.make(uri).save(path)
        print(f'Saved {path}')
