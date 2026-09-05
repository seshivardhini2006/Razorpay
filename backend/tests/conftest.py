import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# db.DB_PATH is resolved the first time `db` is imported anywhere in the suite
# (including engine imports during collection), so point it at a unique,
# per-session temp file *before* any test module can import it. This keeps the
# whole suite on one consistent, fresh database regardless of import order.
os.environ["REVIVE_DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="revive_test_"), "revive_test.db"
)