"""B10: session vector deletion is serverless-correct and failures are no longer swallowed.

The old `_delete_vectors_sync` issued a delete-by-metadata-filter (rejected by serverless indexes)
and wrapped it in `try/except: log` INSIDE the @retry — so tenacity never retried and `/api/cleanup`
always reported success while deleting nothing. The fix enumerates the session's vector ids by
prefix and deletes by id (serverless-supported), letting errors propagate.
"""

from unittest.mock import MagicMock

import pytest

from database.db_manager import PineconeClient


def _client_with_index(index: MagicMock) -> PineconeClient:
    client = PineconeClient(api_key="test", index_name="idx")
    client._index = index  # bypass _index_or_raise (no real Pinecone)
    return client


def test_delete_enumerates_by_prefix_and_deletes_ids():
    index = MagicMock()
    # `list` yields pages (lists) of ids for the session prefix
    index.list.side_effect = lambda **kw: iter([["sess1_f_0000", "sess1_f_0001"]])
    client = _client_with_index(index)

    client._delete_vectors_sync("sess1")

    index.list.assert_called_once_with(prefix="sess1_")
    index.delete.assert_called_once_with(ids=["sess1_f_0000", "sess1_f_0001"])


def test_delete_no_vectors_is_a_noop():
    index = MagicMock()
    index.list.side_effect = lambda **kw: iter([])
    client = _client_with_index(index)

    client._delete_vectors_sync("sess1")

    index.delete.assert_not_called()


def test_delete_failure_propagates_and_is_not_swallowed():
    index = MagicMock()
    index.list.side_effect = lambda **kw: iter([["sess1_f_0000"]])
    index.delete.side_effect = RuntimeError("serverless rejected the delete")
    client = _client_with_index(index)

    # The error must escape (so the caller can't report a false "cleaned"); @retry reraises.
    with pytest.raises(RuntimeError):
        client._delete_vectors_sync("sess1")
