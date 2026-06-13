"""IAM Gateway CLI – interactive chat client.

Usage:
    uv run python -m cli.main --username alice
    COGNITO_USERNAME=alice uv run python -m cli.main

Required env vars:
    CHAT_API_URL          Full /chat endpoint URL from terraform output chat_endpoint
    COGNITO_USER_POOL_ID  Cognito User Pool ID
    COGNITO_CLIENT_ID     Cognito App Client ID

Optional env vars:
    COGNITO_USERNAME      Default username (overridden by --username flag)
    AWS_REGION            AWS region (default: eu-central-1)

Special commands during chat:
    /exit   or /quit  – end the session
    /logout           – clear stored tokens and exit
    /session          – print current session ID
"""

import argparse
import getpass
import os
import sys

from cli.auth import AuthError, AuthTokens, CognitoConfig, login, refresh
from cli.gateway import GatewayError, send_message
from cli.scan import client_scan, format_scan_warning
from cli.storage import clear_tokens, load_tokens, needs_refresh, save_tokens


def get_api_url() -> str:
    url = os.environ.get("CHAT_API_URL", "").strip()
    if not url:
        sys.exit("Error: CHAT_API_URL environment variable is not set.\n"
                 "Set it to the 'chat_endpoint' value from terraform output.")
    return url


def prompt_login(
    username: str,
    config: CognitoConfig,
    password_getter=getpass.getpass,
) -> AuthTokens:
    print(f"Login required for '{username}'.")
    password = password_getter("Password: ")
    try:
        tokens = login(username, password, config)
        save_tokens(username, tokens)
        print("Login successful.")
        return tokens
    except AuthError as exc:
        sys.exit(f"Login failed: {exc}")


def ensure_tokens(username: str, config: CognitoConfig, password_getter=getpass.getpass):
    """Return valid AuthTokens, refreshing or re-logging-in as needed."""
    tokens = load_tokens(username)

    if tokens is not None and not needs_refresh(username):
        return tokens

    # Attempt silent refresh if we have a refresh token
    if tokens is not None:
        try:
            new_tokens = refresh(tokens.refresh_token, config)
            save_tokens(username, new_tokens)
            return new_tokens
        except AuthError:
            pass  # refresh token expired — fall through to interactive login

    return prompt_login(username, config, password_getter=password_getter)


def run_chat_loop(
    username: str,
    config: CognitoConfig,
    api_url: str,
    password_getter=getpass.getpass,
    input_fn=input,
    print_fn=print,
) -> None:
    """Main interactive chat loop (injectable I/O for testing)."""
    tokens = ensure_tokens(username, config, password_getter=password_getter)
    session_id: str | None = None

    print_fn(f"\nConnected as '{username}'. Commands: /exit /logout /session\n")

    while True:
        try:
            user_input = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print_fn("\nGoodbye.")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            print_fn("Goodbye.")
            break

        if user_input == "/logout":
            clear_tokens(username)
            print_fn("Logged out. Tokens cleared from credential store.")
            break

        if user_input == "/session":
            print_fn(f"Session ID: {session_id or '(new – first message not sent yet)'}")
            continue

        # Client-side defense-in-depth scan
        scan_result = client_scan(user_input)
        if not scan_result.is_clean:
            print_fn(f"[BLOCKED] {format_scan_warning(scan_result)}")
            continue

        if scan_result.has_pii:
            print_fn("[WARNING] PII detected and redacted before sending.")

        # Proactive token refresh before the network call
        if needs_refresh(username):
            tokens = ensure_tokens(username, config, password_getter=password_getter)

        try:
            resp = send_message(
                message=scan_result.redacted_text,
                id_token=tokens.id_token,
                api_url=api_url,
                session_id=session_id,
            )
        except GatewayError as exc:
            if exc.status_code == 401:
                # Token invalid mid-session (e.g. Cognito key rotation)
                print_fn("[INFO] Session token rejected — please re-authenticate.")
                tokens = prompt_login(username, config, password_getter=password_getter)
                continue
            print_fn(f"[ERROR] {exc}")
            continue

        session_id = resp.session_id
        print_fn(f"\n{resp.response}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="IAM Gateway CLI chat client")
    parser.add_argument(
        "--username", "-u",
        default=os.environ.get("COGNITO_USERNAME", ""),
        help="Cognito username (or set COGNITO_USERNAME env var)",
    )
    args = parser.parse_args()

    username = args.username.strip()
    if not username:
        username = input("Username: ").strip()
    if not username:
        sys.exit("Error: username is required.")

    try:
        config = CognitoConfig.from_env()
    except EnvironmentError as exc:
        sys.exit(f"Configuration error: {exc}")

    api_url = get_api_url()
    run_chat_loop(username, config, api_url)


if __name__ == "__main__":
    main()
