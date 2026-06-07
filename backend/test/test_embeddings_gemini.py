from unittest.mock import patch

import google.generativeai as genai
import pytest
from langchain_text_splitters import RecursiveCharacterTextSplitter

from components.preprocessing import DocumentParser


@patch("components.preprocessing.DocumentParser.extract_content")
@patch("google.generativeai.embed_content")
def test_embedding_pipeline(mock_embed_content, mock_extract_content):
    # Arrange
    mock_extract_content.return_value = "This is a test document. " * 100
    mock_embed_content.return_value = {"embedding": [[0.1] * 768]}

    EMBEDDING_MODEL = "gemini-embedding-001"
    EMBEDDING_DIM = 768

    pdf_path = "dummy.pdf"
    filename = "dummy.pdf"

    # Act
    text = DocumentParser.extract_content(pdf_path, filename)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(text)

    batch = chunks[:1]
    res = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=batch,
        task_type="retrieval_document",
        output_dimensionality=EMBEDDING_DIM,
    )
    embs = res["embedding"]

    # Assert
    assert len(chunks) > 0
    assert len(embs) == 1
    assert len(embs[0]) == EMBEDDING_DIM
    mock_extract_content.assert_called_once_with(pdf_path, filename)
    mock_embed_content.assert_called_once()
