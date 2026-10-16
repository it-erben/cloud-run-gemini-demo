import json
import time
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from google.auth import crypt, jwt
from google.auth.exceptions import GoogleAuthError

from google_login import verify_google_token


class IdentitySDKTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Google key")])
        now = datetime.now(UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        cls.cert = certificate.public_bytes(serialization.Encoding.PEM).decode()
        cls.signer = crypt.RSASigner.from_string(private, key_id="test-key")

    def token(self, **overrides):
        return jwt.encode(
            self.signer,
            {
                "iss": "https://accounts.google.com",
                "aud": "demo.apps.googleusercontent.com",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "sub": "123456",
                "nonce": "one-time-nonce",
                **overrides,
            },
        ).decode()

    def verify(self, token):
        response = SimpleNamespace(
            status=200, data=json.dumps({"test-key": self.cert}).encode()
        )
        with patch("google_login.Request") as transport:
            transport.return_value.return_value = response
            claims = verify_google_token(token, "demo.apps.googleusercontent.com")
            call = transport.return_value.call_args
            self.assertEqual(call.kwargs["timeout"], 10)
            self.assertEqual(call.args[0], "https://www.googleapis.com/oauth2/v1/certs")
        return claims

    def test_real_sdk_verifies_signature_and_claims(self):
        claims = self.verify(self.token())
        self.assertEqual(claims["sub"], "123456")
        self.assertEqual(claims["nonce"], "one-time-nonce")

    def test_real_sdk_rejects_wrong_audience_issuer_and_expired_token(self):
        for overrides in (
            {"aud": "other-client"},
            {"iss": "https://evil.example"},
            {"exp": int(time.time()) - 10},
        ):
            with (
                self.subTest(overrides=overrides),
                self.assertRaises((ValueError, GoogleAuthError)),
            ):
                self.verify(self.token(**overrides))

    def test_real_sdk_rejects_changed_signature(self):
        header, payload, signature = self.token().split(".")
        changed = ("A" if signature[0] != "A" else "B") + signature[1:]
        with self.assertRaises(ValueError):
            self.verify(f"{header}.{payload}.{changed}")
