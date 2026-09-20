"""Create local deployment secrets without printing or overwriting them."""
import os
from pathlib import Path
import secrets


def main():
    target = Path(__file__).resolve().parents[1] / ".env"
    values = {key: secrets.token_hex(32) for key in ("POSTGRES_PASSWORD", "READER_TOKEN", "REVIEWER_TOKEN", "ADMIN_TOKEN")}
    values.update(APP_VERSION="local", CRITERIA_ROOT="/home/ubuntu/criteria", MEDICAL_LIBRARY_ROOT="/home/ubuntu/rclone/papers")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("ENV_EXISTS: unchanged")
        return
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
    print("ENV_CREATED: secrets stored locally, not displayed")


if __name__ == "__main__":
    main()
