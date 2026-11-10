"""Hashes of every vault-template/protocol file version Juno has shipped.

A vault copy equal to one of these (and different from the current template) was never edited
by hand, so `juno migrate --protocol` may replace it. When you edit a protocol template, append
the new version's hash here and never remove old ones (tests enforce the append).
"""

import hashlib

SHIPPED: dict[str, tuple[str, ...]] = {
    "backlog.md": (
        "fc564ea12783546eee9cfa0dd11d514487260e3e53fb0e653d388265ca746075",
    ),
    "handoffs.md": (
        "2a4d8f5cd4bf2ecd58bd2e33bef5fab1020b4a0eb1823b451eef86cfb0f36ef1",
        "73cffd01b5dac3251e426a12695b3c33c58b040d7c34304bebad030e7ba2e77d",
    ),
    "ideas.md": (
        "722d5ce96d710b3fed6e1b7a1e4cb4c7289cd5d4abded1710107662d5db87b70",
    ),
    "links.md": (
        "52f6a64781690a136a4638017b1b6a9d5089520b898e9f485d1839fb3ac51dae",
    ),
    "rules.md": (
        "5b70d10e0727bd2763787ae4314c9b445e9e5d3584cf0a15f2666807bff8ae47",
        "5d0b7d46a8e9e6308286b8185f65aba3fbc6a64dee508d0c0e06f2a8aa6d9c8d",
    ),
    "vault-files.md": (
        "02b4850bd756fe2d2b96f6f3bb91beb674a66bf4ef2832424d3415ac2873363e",
        "f1edbf7f3ef1d69b4784b92e577f525b26805816d555cf5471bf67449f3d0355",
        "58f5dc767b64ee9430b8ed747c221f45f6dff5041f9521c00195819d5be0acf5",
        "66221b5e0ad67d26916d9b4df2bc075c2564a6426b0e98116b550ab56949bf3b",
        "d8c74662908a1f787dc0564808df7ae45e504cef58663d32c5a0eea6886dd9ca",
    ),
}


def digest(text: str) -> str:
    normal = text.replace("\r\n", "\n").rstrip()
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()


def classify(name: str, template: str, vault: str | None) -> str:
    """current | outdated (an unedited older shipped version) | modified | missing."""
    if vault is None:
        return "missing"
    have = digest(vault)
    if have == digest(template):
        return "current"
    return "outdated" if have in SHIPPED.get(name, ()) else "modified"
