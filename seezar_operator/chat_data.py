from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Dict, List

import pandas as pd

REF_COL = "Chat Ref"
ROLE_COL = "User/Assistant"
MSG_COL = "Message"


def load_export(zip_path: Path) -> pd.DataFrame:
    frames = []
    with zipfile.ZipFile(zip_path) as zf:
        # A dealership group ships one CSV per franchise; all of them belong to it.
        names = sorted(n for n in zf.namelist() if n.lower().endswith(".csv"))
        if not names:
            raise ValueError("No CSV found inside %s" % zip_path.name)
        for name in names:
            # utf-8-sig strips the BOM that would otherwise corrupt the first column name.
            frame = pd.read_csv(io.BytesIO(zf.read(name)), encoding="utf-8-sig")
            missing = {REF_COL, MSG_COL} - set(frame.columns)
            if missing:
                raise ValueError(
                    "%s is missing expected column(s): %s" % (name, sorted(missing))
                )
            frames.append(frame)

    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    df.attrs["source_csvs"] = names
    return df


def messages_per_chat(df: pd.DataFrame) -> Dict[str, float]:
    total_rows = int(len(df))
    unique_refs = int(df[REF_COL].nunique(dropna=True))
    if unique_refs == 0:
        raise ValueError("Export contains no Chat Ref values")
    return {
        "total_rows": total_rows,
        "unique_chat_refs": unique_refs,
        "messages_per_chat": round(total_rows / unique_refs, 2),
    }


def customer_messages(df: pd.DataFrame) -> List[str]:
    if ROLE_COL in df.columns:
        mask = df[ROLE_COL].astype(str).str.strip().str.lower().eq("user")
        rows = df.loc[mask, MSG_COL]
    else:
        rows = df[MSG_COL]
    return [str(m).strip() for m in rows.dropna() if str(m).strip()]
