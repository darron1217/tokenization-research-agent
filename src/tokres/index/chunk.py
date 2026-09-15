"""Character-based chunking suited to mixed Korean/English, with a contextual header on every chunk."""
from __future__ import annotations

import re
from dataclasses import dataclass

TARGET = 900
HARD_MAX = 1400
OVERLAP = 120
_SENT_END = re.compile(r"(?<=[.!?。])\s+|(?<=다\.)\s+|(?<=요\.)\s+")


@dataclass
class Chunk:
    index: int
    text: str  # body only
    header: str  # contextual header prepended for embedding


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        out.extend(s.strip() for s in _SENT_END.split(para) if s.strip())
    return out


def chunk_text(text: str, header: str, target: int = TARGET, hard_max: int = HARD_MAX, overlap: int = OVERLAP) -> list[Chunk]:
    sents = split_sentences(text)
    chunks: list[Chunk] = []
    buf: list[str] = []
    size = 0
    idx = 1  # 0 is reserved for the summary chunk

    def flush() -> None:
        nonlocal buf, size, idx
        if not buf:
            return
        body = " ".join(buf).strip()
        chunks.append(Chunk(index=idx, text=body, header=header))
        idx += 1
        # overlap: keep trailing sentences up to `overlap` chars
        keep: list[str] = []
        acc = 0
        for s in reversed(buf):
            if acc + len(s) > overlap:
                break
            keep.insert(0, s)
            acc += len(s)
        buf = keep
        size = acc

    for s in sents:
        while len(s) > hard_max:  # pathological long sentence → hard split
            buf.append(s[:hard_max])
            size += hard_max
            flush()
            s = s[hard_max:]
        if size + len(s) > target and buf:
            flush()
        buf.append(s)
        size += len(s)
    if buf and (not chunks or " ".join(buf).strip() != chunks[-1].text):
        body = " ".join(buf).strip()
        if len(body) > overlap or not chunks:
            chunks.append(Chunk(index=idx, text=body, header=header))
    return chunks


def make_header(title: str, entity: str | None, doc_type: str | None, date: str | None, summary_line: str | None = None) -> str:
    h = f"{title} | {entity or '-'} | {doc_type or '-'} | {date or '-'}"
    if summary_line:
        h += f"\n{summary_line[:200]}"
    return h
