import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seezar_operator.chat_data import (  # noqa: E402
    customer_messages, load_export, messages_per_chat,
)

CRLF = chr(13) + chr(10)

CSV = (
    "Chat Ref,Date,Time,User/Assistant,Message,Feedback\r\n"
    '#111,2025-06-09,14:20:52,user,"Hej!\nHar I en Porsche?",\r\n'
    "#111,2025-06-09,14:21:04,assistant,Ja det har vi,\r\n"
    "#111,2025-06-09,14:22:00,user,Hvad koster den?,\r\n"
    "#222,2025-06-10,10:00:00,user,show me some SUVs,\r\n"
    "#222,2025-06-10,10:00:30,assistant,Here are some options,\r\n"
)


@pytest.fixture
def export_zip(tmp_path):
    path = tmp_path / "chat-history.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Ejner Hessel.csv", CSV.encode("utf-8-sig"))
    return path


def test_load_export_handles_bom(export_zip):
    df = load_export(export_zip)
    assert list(df.columns)[0] == "Chat Ref"


def test_embedded_newlines_do_not_inflate_row_count(export_zip):
    raw = zipfile.ZipFile(export_zip).read("Ejner Hessel.csv").decode("utf-8-sig")
    assert len(raw.splitlines()) > 6, "fixture should contain an embedded newline"
    assert len(load_export(export_zip)) == 5


def test_messages_per_chat(export_zip):
    stats = messages_per_chat(load_export(export_zip))
    assert stats["total_rows"] == 5
    assert stats["unique_chat_refs"] == 2
    assert stats["messages_per_chat"] == 2.5


def test_customer_messages_excludes_bot_replies(export_zip):
    msgs = customer_messages(load_export(export_zip))
    assert len(msgs) == 3
    assert all("Here are some options" not in m for m in msgs)


def test_missing_column_is_rejected(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bad.csv", "Foo,Bar\r\n1,2\r\n")
    with pytest.raises(ValueError, match="missing expected column"):
        load_export(path)


def test_zero_refs_is_rejected():
    df = pd.DataFrame({"Chat Ref": pd.Series(dtype=object), "Message": pd.Series(dtype=object)})
    with pytest.raises(ValueError, match="no Chat Ref"):
        messages_per_chat(df)


def test_all_csvs_in_the_zip_are_combined(tmp_path):
    path = tmp_path / "chat-history.zip"
    second = (
        "Chat Ref,Date,Time,User/Assistant,Message,Feedback" + CRLF +
        "#333,2025-06-11,09:00:00,user,Peugeot spoergsmaal," + CRLF
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Norton Way.csv", CSV.encode("utf-8-sig"))
        zf.writestr("Norton Way Peugeot.csv", second.encode("utf-8-sig"))
    stats = messages_per_chat(load_export(path))
    assert stats["total_rows"] == 6
    assert stats["unique_chat_refs"] == 3
