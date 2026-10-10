"""Load .txt/.md files, chunk, embed with the configured provider, and store in Qdrant."""
import hashlib
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / "mcp-server" / ".env")  # reuse the server's settings if present
sys.path.insert(0, str(HERE.parent / "mcp-server"))

from tools.providers import embed_texts, vector_dimension

KB_DIR = Path(os.getenv("KB_DIR", HERE / "knowledge_base")).resolve()
QDRANT_HOST = os.getenv("QDRANT_HOST", "http://localhost:6333")
COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge_base")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "100"))
NAMESPACE = uuid.UUID("6f1c2f0e-5b1a-4c55-9d0e-2b7a9f3a1c11")  # fixed -> deterministic IDs


def fail(msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_files():
    if not KB_DIR.is_dir():
        fail(f"Knowledge base directory not found: {KB_DIR}")
    files = []
    for p in sorted(KB_DIR.rglob("*")):
        if p.suffix.lower() not in (".txt", ".md") or not p.is_file():
            continue
        rp = p.resolve()
        if KB_DIR not in rp.parents:  # block symlinks that escape KB_DIR
            print(f"skip (outside KB_DIR): {p}")
            continue
        try:
            text = rp.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as e:
            print(f"skip (unreadable): {p} ({e.__class__.__name__})")
            continue
        if not text:
            print(f"skip (empty): {p.name}")
            continue
        files.append((rp.relative_to(KB_DIR).as_posix(), text))
    return files


def chunk_text(text: str):
    chunks, start, n = [], 0, len(text)
    while start < n:
        end = min(start + CHUNK_SIZE, n)
        if end < n:  # prefer breaking at whitespace
            ws = text.rfind(" ", start + CHUNK_SIZE // 2, end)
            ws = max(ws, text.rfind("\n", start + CHUNK_SIZE // 2, end))
            if ws > start:
                end = ws
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def embed(texts):
    try:
        vecs = embed_texts(texts)
    except Exception as e:
        fail(f"Embedding failed ({e.__class__.__name__}). Check the configured embedding provider and model.")
    if len(vecs) != len(texts):
        fail("Embedding provider returned an unexpected number of embeddings")
    return vecs


def existing_dim(client):
    cfg = client.get_collection(COLLECTION).config.params.vectors
    return cfg.size if hasattr(cfg, "size") else None  # None => named vectors (unsupported here)


def main():
    files = load_files()
    if not files:
        fail(f"No non-empty .txt/.md files found in {KB_DIR}. Existing Qdrant data was left untouched.")

    records = []  # (source, index, text)
    for source, text in files:
        for i, c in enumerate(chunk_text(text)):
            records.append((source, i, c))
    print(f"{len(files)} files -> {len(records)} chunks")

    vectors = []
    for i in range(0, len(records), 16):
        vectors.extend(embed([r[2] for r in records[i : i + 16]]))
    try:
        dim = vector_dimension(vectors)
    except ValueError:
        fail("Embedding batches returned inconsistent vector dimensions; Qdrant was left unchanged.")
    print(f"Embedding provider returned {dim}-dimensional vectors")

    try:
        client = QdrantClient(
            url=QDRANT_HOST,
            api_key=os.getenv("QDRANT_API_KEY") or None,
            timeout=30,
        )
        if client.collection_exists(COLLECTION):
            have = existing_dim(client)
            if have != dim:
                fail(
                    f"Collection '{COLLECTION}' has vector size {have} but the model produces {dim}. "
                    "Choose a new QDRANT_COLLECTION for a separate migration; existing data was left unchanged."
                )
        else:
            client.create_collection(COLLECTION, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
            print(f"Created collection '{COLLECTION}'")

        points, keep = [], set()
        for (source, idx, text), vec in zip(records, vectors):
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
            pid = str(uuid.uuid5(NAMESPACE, f"{source}|{idx}|{digest}"))
            keep.add(pid)
            points.append(PointStruct(id=pid, vector=vec, payload={"text": text, "source": source, "chunk_index": idx}))
        for i in range(0, len(points), 64):
            client.upsert(COLLECTION, points=points[i : i + 64])

        # remove stale points (from edited/deleted files) - only inside THIS collection
        stale, offset = [], None
        while True:
            batch, offset = client.scroll(COLLECTION, limit=500, offset=offset, with_payload=False, with_vectors=False)
            stale += [p.id for p in batch if str(p.id) not in keep]
            if offset is None:
                break
        if stale:
            client.delete(COLLECTION, points_selector=stale)
        total = client.count(COLLECTION, exact=True).count
    except SystemExit:
        raise
    except Exception as e:
        fail(f"Qdrant operation failed ({e.__class__.__name__}). Is Qdrant running at {QDRANT_HOST}?")

    print(f"Done. Upserted {len(points)} chunks, removed {len(stale)} stale. Collection now has {total} vectors.")


if __name__ == "__main__":
    main()