"""Per-mind call limits that survive reloads and are shared by every process serving the same data folder.

The window is kept in ``<data>/rate.sqlite`` (one row per allowed call: actor id and wall-clock time; no query, no
text). Each check is one ``BEGIN IMMEDIATE`` transaction, so two processes cannot both take the last call of a window.
If the ledger cannot be read or written the call is refused: a limit that cannot be checked is not granted.

This is the app's own guard. A host-enforced limit on the attachment policy is still host seam S2 (README).
"""

import pathlib
import sqlite3
import time

WINDOW_SECONDS = 60


def ledger_path(data_dir):
    return pathlib.Path(data_dir) / "rate.sqlite"


def allow(data_dir, actor_id, limit, window=WINDOW_SECONDS, now=None):
    """True and the call is recorded, or False when ``actor_id`` already made ``limit`` calls in the window."""
    now = time.time() if now is None else now
    try:
        db = sqlite3.connect(ledger_path(data_dir), timeout=5, isolation_level=None)
    except sqlite3.Error:
        return False
    try:
        db.execute("CREATE TABLE IF NOT EXISTS calls(actor TEXT NOT NULL, at REAL NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS calls_actor ON calls(actor, at)")
        db.execute("BEGIN IMMEDIATE")
        # Expired rows go; so do rows dated beyond the window ahead, which only a clock set back can leave.
        db.execute("DELETE FROM calls WHERE at <= ? OR at > ?", (now - window, now + window))
        (count,) = db.execute("SELECT COUNT(*) FROM calls WHERE actor = ?", (str(actor_id),)).fetchone()
        if count >= limit:
            db.execute("COMMIT")
            return False
        db.execute("INSERT INTO calls VALUES (?, ?)", (str(actor_id), now))
        db.execute("COMMIT")
        return True
    except sqlite3.Error:
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return False
    finally:
        db.close()
