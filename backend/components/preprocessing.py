"""Master ingestion pipeline: S3 download → parse → chunk → embed → Pinecone upsert.

All external calls go through injected client instances; no module-level singletons.
"""

import os
from typing import TYPE_CHECKING

import structlog
from langchain_text_splitters import RecursiveCharacterTextSplitter

from database import repository as repo
from database.doc_parser import DocumentParser
from database.models import DocumentStatus

if TYPE_CHECKING:
    from database.db_manager import PineconeClient
    from integrations.huggingface.client import HuggingFaceClient
    from integrations.s3.client import S3Client

logger = structlog.get_logger(__name__)


async def _set_status(session_factory, s3_key: str, status: "DocumentStatus") -> None:
    """Open a short-lived session, set the document's ingestion status, and commit."""
    async with session_factory() as db:
        await repo.set_document_status(db, s3_key=s3_key, status=status)
        await db.commit()


async def process_file_pipeline(
    file_key: str,
    filename: str,
    session_id: str,
    s3: "S3Client",
    embedder: "HuggingFaceClient",
    pinecone: "PineconeClient",
    session_factory,
    user_id: str | None = None,
) -> str:
    """
    1. Mark document PROCESSING in Postgres
    2. Download from S3
    3. Extract text
    4. Chunk
    5. Embed with HuggingFace
    6. Save to Pinecone
    7. Mark document READY (or FAILED on error)
    """
    temp_path = None
    try:
        logger.info("ingestion_start", filename=filename, s3_key=file_key)

        await _set_status(session_factory, file_key, DocumentStatus.PROCESSING)

        temp_path = await s3.download_to_temp(file_key)
        logger.info("ingestion_downloaded", temp_path=temp_path)

        raw_text = DocumentParser.extract_content(temp_path, filename)
        logger.info("ingestion_extracted", chars=len(raw_text))

        if not raw_text.strip():
            logger.info("ingestion_empty", reason="no text extracted from document")
            await _set_status(session_factory, file_key, DocumentStatus.READY)
            return ""

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200, separators=["\n\n", "\n", ". ", " ", ""]
        )
        chunks = splitter.split_text(raw_text)
        logger.info("ingestion_chunked", chunks=len(chunks))

        if not chunks:
            logger.info("ingestion_empty", reason="no valid chunks created")
            await _set_status(session_factory, file_key, DocumentStatus.READY)
            return raw_text

        logger.info("ingestion_embedding_start")
        embeddings = await embedder.embed_batch(chunks, batch_size=32)
        logger.debug(
            "ingestion_embeddings",
            count=len(embeddings),
            dims=len(embeddings[0]) if embeddings else 0,
        )

        if len(embeddings) != len(chunks):
            logger.error(
                "ingestion_embedding_mismatch",
                embedding_count=len(embeddings),
                chunk_count=len(chunks),
            )
            raise ValueError("Embedding mismatch")

        vectors = [
            {
                "id": f"{session_id}_{filename.replace(' ', '_')}_{i:04d}",
                "values": embedding,
                "metadata": {
                    "text": chunk,
                    "filename": filename,
                    "session_id": session_id,
                    "chunk_index": i,
                    "s3_key": file_key,
                    # Tenant scoping: stamp the owner so search can filter by user_id. Pinecone
                    # rejects null metadata, so only set it when the owner is known.
                    **({"user_id": user_id} if user_id else {}),
                },
            }
            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=False))
        ]

        await pinecone.save_vectors(vectors)
        logger.info("ingestion_complete", vectors_saved=len(vectors))

        await _set_status(session_factory, file_key, DocumentStatus.READY)

        return raw_text  # Phase 7: hand the parsed text back for the entity-extraction pass

    except Exception as e:
        logger.error("ingestion_failed", error=str(e), exc_info=True)
        try:
            await _set_status(session_factory, file_key, DocumentStatus.FAILED)
        except Exception:
            logger.error("ingestion_status_update_failed", exc_info=True)
        raise

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
            logger.info("ingestion_temp_cleanup", temp_path=temp_path)
