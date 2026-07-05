"""Thin wrapper around instagrapi's Client.

Handles login (with optional 2FA), session persistence (so you do not have to
log in every run, which also greatly reduces the chance of Instagram
challenges), and fetching saved collections.
"""

from __future__ import annotations

import os
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    TwoFactorRequired,
)

# Instagram's built-in "All Posts" pseudo-collection.
ALL_MEDIA_COLLECTION_ID = "ALL_MEDIA_AUTO_COLLECTION"


class LoginError(Exception):
    """Raised with a user-friendly message when login fails."""


class InstagramClient:
    def __init__(self, session_file: Path):
        self.session_file = Path(session_file)
        self.client = Client()
        # Small randomized delay between API requests keeps us polite and
        # far away from rate limits.
        self.client.delay_range = [1, 3]

    # ------------------------------------------------------------------ auth

    def login(self, username: str, password: str, verification_code: str = "") -> str:
        """Log in, reusing a saved session when possible.

        Returns the logged-in username. Raises LoginError on failure.
        """
        try:
            return self._login_inner(username, password, verification_code)
        except LoginError:
            raise
        except TwoFactorRequired as exc:
            raise LoginError(
                "Two-factor authentication required. Enter the 6-digit code "
                "from your authenticator app in the 2FA field and try again."
            ) from exc
        except BadPassword as exc:
            raise LoginError("Wrong username or password.") from exc
        except ChallengeRequired as exc:
            raise LoginError(
                "Instagram is asking for a security check on this account. "
                "Open the Instagram app or website, approve the login "
                "attempt / complete the check, then try again."
            ) from exc
        except PleaseWaitFewMinutes as exc:
            raise LoginError(
                "Instagram is rate-limiting login attempts. Please wait a few "
                "minutes and try again."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surface anything else nicely
            raise LoginError(f"Login failed: {exc}") from exc

    def _login_inner(self, username: str, password: str, verification_code: str) -> str:
        if self.session_file.exists():
            try:
                self.client.load_settings(self.session_file)
                self.client.login(
                    username, password, verification_code=verification_code
                )
                # Cheap request to verify the session is actually alive.
                self.client.get_timeline_feed()
                return self._finish_login()
            except LoginRequired:
                # Stale session: keep device uuids (looks like the same
                # phone to Instagram) but drop the dead authorization.
                old = self.client.get_settings()
                self.client.set_settings({})
                self.client.set_uuids(old.get("uuids", {}))
            except (BadPassword, TwoFactorRequired, ChallengeRequired,
                    PleaseWaitFewMinutes):
                raise
            except Exception:
                # Unreadable/corrupt session file: start fresh.
                self.client.set_settings({})

        self.client.login(username, password, verification_code=verification_code)
        return self._finish_login()

    def _finish_login(self) -> str:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.client.dump_settings(self.session_file)
        try:
            # The session file holds auth tokens: owner read/write only.
            os.chmod(self.session_file, 0o600)
        except OSError:
            pass  # best effort (no-op on some Windows filesystems)
        return self.client.username

    # ----------------------------------------------------------- collections

    def named_collections(self) -> list:
        """User's saved collections, excluding the automatic 'All Posts'."""
        collections = self.client.collections()
        return [
            c for c in collections
            if c.id != ALL_MEDIA_COLLECTION_ID
            and c.type != ALL_MEDIA_COLLECTION_ID
        ]

    def collection_medias(self, collection_id: str, last_media_pk: int = 0) -> list:
        """Media in a collection, newest first (amount=0 means no limit).

        With last_media_pk set, Instagram is only asked for items saved
        *after* that pk — an incremental fetch that keeps repeat runs fast.
        """
        return self.client.collection_medias(
            collection_id, amount=0, last_media_pk=last_media_pk
        )

    def all_saved_medias(self, last_media_pk: int = 0) -> list:
        """Every saved media, regardless of collection."""
        return self.client.collection_medias(
            ALL_MEDIA_COLLECTION_ID, amount=0, last_media_pk=last_media_pk
        )
