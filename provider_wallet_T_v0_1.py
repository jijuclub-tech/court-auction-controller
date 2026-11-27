"""Controller provider credentials and Grok invocation broker."""
import argparse
import getpass
import json
import os
import re
import secrets
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROVIDERS = {"deepseek", "gemini"}
AAD = b"BIDGUARD-provider-wallet-v1"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_wallet(path, password):
    from key_wallet import check_private, kdf, open_seal, unb64
    try:
        document = json.loads(check_private(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return json.loads(open_seal(kdf(password, unb64(document["salt"])), document["sealed"], AAD))


def save_wallet(path, password, keys):
    from key_wallet import b64, kdf, seal, write_private
    salt = secrets.token_bytes(16)
    write_private(path, {"salt": b64(salt), "sealed": seal(kdf(password, salt), json.dumps(keys).encode(), AAD)})


def complete(provider, model, prompt, key):
    if provider == "deepseek":
        url = "https://api.deepseek.com/chat/completions"
        headers = {"Authorization": "Bearer " + key}
        body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1024}
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": key}
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 1024}}
    request = Request(url, data=json.dumps(body).encode(), headers={**headers, "Content-Type": "application/json"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        payload = json.loads(response.read(1024 * 1024))
    if provider == "deepseek":
        return payload["choices"][0]["message"]["content"], payload.get("usage", {})
    parts = payload["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts if not part.get("thought")), payload.get("usageMetadata", {})


def route(handler, method, path):
    if method != "POST" or path != "/api/bot/ai/invoke":
        return False
    if not handler._bot():
        handler._json(403, {"ok": False, "error": "bot authentication required"})
        return True
    body = handler._body()
    provider = body.get("provider")
    prompt = body.get("prompt")
    if (not isinstance(provider, str) or provider not in PROVIDERS
            or not isinstance(prompt, str) or not 0 < len(prompt.strip()) <= 12000):
        handler._json(400, {"ok": False, "error": "invalid provider or prompt"})
        return True
    model = os.getenv("BIDGUARD_" + provider.upper() + "_MODEL", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
        handler._json(503, {"ok": False, "error": "provider model not configured"})
        return True
    try:
        path = Path(os.environ["BIDGUARD_PROVIDER_WALLET"])
        password = os.environ["BIDGUARD_PROVIDER_WALLET_PASSWORD"]
        key = read_wallet(path, password).get(provider)
    except (Exception, SystemExit):
        key = None
    if not key:
        handler._json(503, {"ok": False, "error": "provider wallet unavailable"})
        return True
    try:
        answer, usage = complete(provider, model, prompt, key)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("empty response")
    except Exception:
        handler._json(502, {"ok": False, "error": "provider request failed"})
        return True
    handler._json(200, {"ok": True, "provider": provider, "model": model, "text": answer, "usage": usage})
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", type=Path, required=True)
    parser.add_argument("command", choices=["set", "status"])
    parser.add_argument("provider", nargs="?", choices=sorted(PROVIDERS))
    args = parser.parse_args()
    if args.command == "set" and not args.provider:
        parser.error("set requires a provider")
    password = getpass.getpass("지갑 암호: ")
    try:
        if len(password) < 12:
            raise ValueError()
        if not args.wallet.exists() and password != getpass.getpass("새 지갑 암호 확인: "):
            raise ValueError()
        keys = read_wallet(args.wallet, password)
        if args.command == "set":
            key = getpass.getpass("등록할 API 키: ").strip()
            if not key or not key.isascii() or any(c.isspace() for c in key):
                raise ValueError()
            keys[args.provider] = key
            save_wallet(args.wallet, password, keys)
        for provider in sorted(PROVIDERS):
            print(provider + (": 등록됨" if keys.get(provider) else ": 미등록"))
    except Exception:
        parser.exit(1, "지갑 암호, 파일 권한, 입력값을 확인해 주세요.\n")
