"""Serve the private PC trial over pinned TLS; this is not a public cloud service."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import HTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import ssl
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from capture_splat.pc_bridge import BridgeStore, handler, wsl_worker


def main():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='Private IPv4 address reachable from your iPhone.')
    parser.add_argument('--port', type=int, default=8770)
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.host)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        parser.error('Bind only to a specific private IPv4 address.')
    repo = Path(__file__).resolve().parents[1]
    state_dir = repo / 'runs/pc-bridge'
    state_dir.mkdir(parents=True, exist_ok=True)
    config_file = state_dir / 'config.json'
    if not config_file.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Capture Splat PC')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=90))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
                .sign(key, hashes.SHA256()))
        (state_dir / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (state_dir / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        pin = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
        config = {'host': str(address), 'port': args.port, 'pin': pin, 'token': secrets.token_urlsafe(32)}
        config_file.write_text(json.dumps(config))
    config = json.loads(config_file.read_text())
    if config['host'] != args.host or config['port'] != args.port:
        parser.error('PC address changed. Archive the existing bridge config before pairing again.')
    pairing = {'url': f'https://{args.host}:{args.port}', 'pin': config['pin'], 'token': config['token']}
    code = base64.urlsafe_b64encode(json.dumps(pairing).encode()).decode()
    (state_dir / 'connection-code.txt').write_text(code)
    import qrcode
    if not (state_dir / 'connection-qr.png').is_file():
        qrcode.make('capturesplat-pc:' + code).save(state_dir / 'connection-qr.png')
    store = BridgeStore(state_dir / 'scans', wsl_worker(repo),
                        runtime_ready=lambda: (state_dir / 'runtime-ready.json').is_file())
    server = HTTPServer((args.host, args.port), handler(store, config['token']))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(state_dir / 'cert.pem', state_dir / 'key.pem')
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f'PC bridge listening at {pairing["url"]}; connection code saved locally under runs/pc-bridge/.', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
