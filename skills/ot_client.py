"""ot_client.py — DPoP-клиент OpenTask (ES256). Токен + свежая подпись на каждый запрос.

Использование:
  python3 ot_client.py GET /api/agent/me
  python3 ot_client.py GET /api/agent/tasks/cm...
  python3 ot_client.py POST /api/agent/tasks/<id>/bids path/to/payload.json
"""
import urllib.request, urllib.error, json, base64, sys, time, uuid, hashlib, os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

SEC = "/opt/liberti_secrets/opentask.json"
BASE = "https://opentask.ai"


def b64u(b):
    if isinstance(b, str):
        b = b.encode()
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load_priv(pem):
    return serialization.load_pem_private_key(pem.encode(), password=None)


def thumbprint(priv):
    """RFC 7638 JWK thumbprint."""
    n = priv.public_key().public_numbers()
    jwk = {"crv": "P-256", "kty": "EC",
           "x": b64u(n.x.to_bytes(32, "big")),
           "y": b64u(n.y.to_bytes(32, "big"))}
    canon = json.dumps(jwk, separators=(",", ":"), sort_keys=True)
    return b64u(hashlib.sha256(canon.encode()).digest())


def sign(priv, msg):
    der = priv.sign(msg, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def dpop_proof(priv, htm, htu, access_token=None, nonce=None):
    n = priv.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256",
           "x": b64u(n.x.to_bytes(32, "big")),
           "y": b64u(n.y.to_bytes(32, "big"))}
    header = {"typ": "dpop+jwt", "alg": "ES256", "jwk": jwk}
    claims = {"jti": str(uuid.uuid4()), "htm": htm.upper(),
              "htu": htu.split("?")[0].split("#")[0],
              "iat": int(time.time())}
    if access_token:
        claims["ath"] = b64u(hashlib.sha256(access_token.encode()).digest())
    if nonce:
        claims["nonce"] = nonce
    h = b64u(json.dumps(header, separators=(",", ":")))
    p = b64u(json.dumps(claims, separators=(",", ":")))
    return f"{h}.{p}.{sign(priv, f'{h}.{p}'.encode())}"


def request(method, path, payload=None, token_key="access_token", proof_key="key_pem", extra_headers=None):
    sec = json.load(open(SEC))
    tok = sec.get(token_key)
    _pem = sec.get(proof_key)
    # key_pem / recovery_key_pem — это ПУТЬ к файлу, а не сам PEM
    if _pem and _pem.strip().startswith("-----"):
        _pem = _pem
    elif _pem and os.path.exists(_pem):
        _pem = open(_pem).read()
    priv = load_priv(_pem)
    url = BASE + path
    h = {"User-Agent": "AntIren-Liberti/1.0", "Accept": "application/json",
         "DPoP": dpop_proof(priv, method, url, tok)}
    if tok:
        h["Authorization"] = f"DPoP {tok}"
    if extra_headers:
        h.update(extra_headers)
    data = None
    if payload is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    req = urllib.request.Request(url, method=method, headers=h, data=data)
    try:
        r = urllib.request.urlopen(req, timeout=35)
        return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:800], dict(e.headers)
    except Exception as e:
        return "ERR", str(e)[:300], {}


if __name__ == "__main__":
    method = sys.argv[1]
    path = sys.argv[2]
    payload = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None
    st, body, _ = request(method, path, payload)
    print(st)
    print(body[:1500])
