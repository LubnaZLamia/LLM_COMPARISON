"""Create/complete the .env file. Called by run.bat / run.sh; safe to run repeatedly."""
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
PLACEHOLDERS = {"", "sk-or-...", "your-key-here"}


def read_env() -> list[str]:
    if not ENV.exists():
        shutil.copy(EXAMPLE, ENV)
    return ENV.read_text(encoding="utf-8").splitlines()


def get(lines, key):
    for line in lines:
        if line.strip().startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def set_value(lines, key, value):
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            return
    lines.append(f"{key}={value}")


def main():
    lines = read_env()

    if (get(lines, "DJANGO_SECRET_KEY") or "change-me") in {"change-me", ""}:
        set_value(lines, "DJANGO_SECRET_KEY", secrets.token_urlsafe(40))

    key = get(lines, "OPENROUTER_API_KEY") or ""
    if key in PLACEHOLDERS:
        print()
        print("=" * 64)
        print(" OpenRouter API key needed")
        print(" Get one at https://openrouter.ai/keys")
        print(" Paste it below and press Enter.")
        print(" (Press Enter without a key to try the app in mock mode.)")
        print("=" * 64)
        key = input(" API key: ").strip().strip('"').strip("'")
        if key:
            set_value(lines, "OPENROUTER_API_KEY", key)
            set_value(lines, "LLM_MOCK_MODE", "false")
            print(" Saved to .env\n")
        else:
            set_value(lines, "LLM_MOCK_MODE", "true")
            print(" No key entered: starting in mock mode. Run again or edit .env to add one.\n")

    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
