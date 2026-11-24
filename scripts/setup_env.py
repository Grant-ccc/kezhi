"""Generate secrets only for this project's ignored .env; never overwrite it."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / ".env"
if target.exists():
    print("Existing .env preserved.")
else:
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace("SEEKDB_ROOT_PASSWORD=replace-with-local-secret", f"SEEKDB_ROOT_PASSWORD={secrets.token_urlsafe(24)}")
    content = content.replace("DB_PASSWORD=replace-with-local-secret", f"DB_PASSWORD={secrets.token_urlsafe(24)}")
    content = content.replace("JWT_SECRET=replace-with-at-least-32-random-characters", f"JWT_SECRET={secrets.token_urlsafe(48)}")
    with target.open("x", encoding="utf-8") as handle:
        handle.write(content)
    print("Created project .env with random local secrets; values not printed.")
