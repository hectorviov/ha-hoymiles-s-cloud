"""v3 login: pre-insp (salt + nonce) -> Argon2id credential hash -> login."""
from argon2.low_level import Type, hash_secret_raw

from custom_components.hoymiles_nimbus.hoymiles_client import HoymilesClient

SALT = "00112233445566778899aabbccddeeff"


def _server(expected_ch):
    calls = []

    def fake(self, path, payload, extra):
        calls.append(path)
        if path.endswith("pre-insp"):
            return {"status": "0", "message": "success", "data": {"a": SALT, "n": "NONCE"}}
        if path.endswith("3/auth/login"):
            if payload["ch"] == expected_ch and payload["n"] == "NONCE":
                return {"status": "0", "message": "success", "data": {"token": "TOKEN"}}
            return {"status": "7", "message": "Invalid credentials"}
        return {"status": "1", "message": "legacy login disabled"}

    return fake, calls


def _argon2(password):
    return hash_secret_raw(secret=password.encode(), salt=bytes.fromhex(SALT), time_cost=3,
                           memory_cost=32768, parallelism=1, hash_len=32, type=Type.ID).hex()


def test_v3_argon2_login(monkeypatch):
    fake, calls = _server(_argon2("secret"))
    monkeypatch.setattr(HoymilesClient, "_auth_post", fake)
    client = HoymilesClient("user@example.com", "secret", "https://example.invalid/")
    assert client.login() is True
    assert client.token == "TOKEN"
    assert calls == ["iam/pub/3/auth/pre-insp", "iam/pub/3/auth/login"]


def test_wrong_password_reports_every_attempt(monkeypatch):
    fake, _ = _server(_argon2("secret"))
    monkeypatch.setattr(HoymilesClient, "_auth_post", fake)
    client = HoymilesClient("user@example.com", "wrong", "https://example.invalid/")
    try:
        client.login()
    except Exception as err:  # noqa: BLE001
        text = str(err)
    assert "v3/web" in text and "v3/installer" in text and "v0" in text
    assert "wrong" not in text  # the password never ends up in the error/log


def test_debug_log_never_contains_token():
    from custom_components.hoymiles_nimbus.hoymiles_client import _redact
    assert _redact({"Authorization": "TOKEN", "Accept": "x"}) == {"Authorization": "***", "Accept": "x"}
