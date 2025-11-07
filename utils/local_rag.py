"""
Local RAG System using Ollama
On-device document Q&A with no cloud dependencies
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
import json
import requests


@dataclass
class RAGDocument:
    """Document to be indexed in the RAG system"""
    content: str
    metadata: Dict[str, Any]
    doc_id: str


@dataclass
class RAGResult:
    """Result from RAG query"""
    answer: str
    sources: List[Dict[str, Any]]
    context_used: str
    query: str
    model_used: str


class LocalEmbeddings:
    """Local embeddings using sentence-transformers (no API calls)"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize local embeddings.

        Args:
            model_name: HuggingFace model name for embeddings
                       Default: all-MiniLM-L6-v2 (fast, 384 dims, 22MB)
                       Alternative: all-mpnet-base-v2 (better quality, 768 dims, 420MB)
        """
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        """Lazy load the embedding model"""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
            except ImportError:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )
        return self._model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of documents"""
        model = self._load_model()
        embeddings = model.encode(texts, show_progress_bar=False)
        return embeddings.tolist()

    def embed_query(self, text: str) -> List[float]:
        """Embed a single query"""
        model = self._load_model()
        embedding = model.encode([text], show_progress_bar=False)[0]
        return embedding.tolist()


class OllamaLLM:
    """Ollama LLM interface for local inference"""

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 2048
    ):
        """
        Initialize Ollama LLM.

        Args:
            model: Ollama model name (llama3.2, mistral, phi, etc.)
            base_url: Ollama server URL
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
        """
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(self, prompt: str, stream: bool = False) -> str:
        """
        Generate text using Ollama.

        Args:
            prompt: Input prompt
            stream: Whether to stream the response

        Returns:
            Generated text
        """
        url = f"{self.base_url}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens
            }
        }

        try:
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()

            if stream:
                # Handle streaming response
                full_response = ""
                for line in response.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        if 'response' in chunk:
                            full_response += chunk['response']
                return full_response
            else:
                # Non-streaming response
                result = response.json()
                return result.get('response', '')

        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Make sure Ollama is running: 'ollama serve'"
            )
        except requests.exceptions.Timeout:
            raise TimeoutError(
                f"Ollama request timed out. Model '{self.model}' may be slow or not available."
            )
        except Exception as e:
            raise RuntimeError(f"Ollama generation failed: {str(e)}")

    def list_models(self) -> List[str]:
        """List available Ollama models"""
        url = f"{self.base_url}/api/tags"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            models = response.json().get('models', [])
            return [m['name'] for m in models]
        except Exception:
            return []


class LocalRAGSystem:
    """
    On-device RAG system using Ollama and local embeddings.
    No cloud APIs, everything runs locally.
    """

    def __init__(
        self,
        ollama_model: str = "llama3.2",
        ollama_url: str = "http://localhost:11434",
        embedding_model: str = "all-MiniLM-L6-v2",
        persist_directory: str = "./local_rag_db",
        chunk_size: int = 500,
        chunk_overlap: int = 50
    ):
        """
        Initialize the local RAG system.

        Args:
            ollama_model: Ollama model for generation
            ollama_url: Ollama server URL
            embedding_model: Sentence-transformers model for embeddings
            persist_directory: Directory to persist vector database
            chunk_size: Size of text chunks for indexing
            chunk_overlap: Overlap between chunks
        """
        self.ollama_model = ollama_model
        self.ollama_url = ollama_url
        self.persist_directory = persist_directory
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # Initialize components
        self.embeddings = LocalEmbeddings(model_name=embedding_model)
        self.llm = OllamaLLM(model=ollama_model, base_url=ollama_url)

        # Initialize vector store
        self.vectorstore = None
        self._init_vectorstore()

    def _init_vectorstore(self):
        """Initialize ChromaDB vector store"""
        try:
            import chromadb
            from chromadb.config import Settings

            # Create persist directory if it doesn't exist
            os.makedirs(self.persist_directory, exist_ok=True)

            # Initialize Chroma client
            self.chroma_client = chromadb.PersistentClient(
                path=self.persist_directory,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )

            # Get or create collection
            self.collection = self.chroma_client.get_or_create_collection(
                name="documents",
                metadata={"description": "DeepSeek-OCR processed documents"}
            )

        except ImportError:
            raise ImportError(
                "chromadb not installed. Install with: pip install chromadb"
            )

    def _chunk_text(self, text: str, metadata: Dict[str, Any]) -> List[RAGDocument]:
        """
        Split text into chunks for indexing.

        Args:
            text: Text to chunk
            metadata: Metadata for the document

        Returns:
            List of RAGDocument chunks
        """
        chunks = []
        start = 0
        text_length = len(text)
        chunk_id = 0

        while start < text_length:
            end = start + self.chunk_size
            chunk_text = text[start:end]

            if chunk_text.strip():
                chunk_metadata = {
                    **metadata,
                    'chunk_id': chunk_id,
                    'start_char': start,
                    'end_char': end
                }

                doc_id = f"{metadata.get('filename', 'unknown')}_{chunk_id}"

                chunks.append(RAGDocument(
                    content=chunk_text,
                    metadata=chunk_metadata,
                    doc_id=doc_id
                ))

                chunk_id += 1

            start = end - self.chunk_overlap

        return chunks

    def add_documents(
        self,
        texts: List[str],
        metadatas: List[Dict[str, Any]]
    ) -> int:
        """
        Add documents to the RAG system.

        Args:
            texts: List of document texts
            metadatas: List of metadata dicts for each document

        Returns:
            Number of chunks added
        """
        all_chunks = []

        # Chunk all documents
        for text, metadata in zip(texts, metadatas):
            chunks = self._chunk_text(text, metadata)
            all_chunks.extend(chunks)

        if not all_chunks:
            return 0

        # Prepare data for Chroma
        ids = [chunk.doc_id for chunk in all_chunks]
        documents = [chunk.content for chunk in all_chunks]
        metadatas_list = [chunk.metadata for chunk in all_chunks]

        # Generate embeddings
        embeddings = self.embeddings.embed_documents(documents)

        # Add to vector store
        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas_list,
            embeddings=embeddings
        )

        return len(all_chunks)

    def query(
        self,
        question: str,
        k: int = 5,
        include_sources: bool = True
    ) -> RAGResult:
        """
        Query the RAG system with a question.

        Args:
            question: User's question
            k: Number of relevant chunks to retrieve
            include_sources: Whether to include source information

        Returns:
            RAGResult with answer and sources
        """
        # Check if collection has documents
        count = self.collection.count()
        if count == 0:
            return RAGResult(
                answer="No documents have been indexed yet. Please add documents first.",
                sources=[],
                context_used="",
                query=question,
                model_used=self.ollama_model
            )

        # Embed the query
        query_embedding = self.embeddings.embed_query(question)

        # Search vector store
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(k, count)
        )

        # Extract results
        documents = results['documents'][0] if results['documents'] else []
        metadatas = results['metadatas'][0] if results['metadatas'] else []

        if not documents:
            return RAGResult(
                answer="No relevant information found in the indexed documents.",
                sources=[],
                context_used="",
                query=question,
                model_used=self.ollama_model
            )

        # Build context from retrieved documents
        context = "\n\n".join([
            f"[Document {i+1}]:\n{doc}"
            for i, doc in enumerate(documents)
        ])

        # Build prompt for LLM
        prompt = self._build_prompt(question, context)

        # Generate answer using Ollama
        answer = self.llm.generate(prompt)

        # Build sources
        sources = []
        if include_sources:
            for doc, meta in zip(documents, metadatas):
                sources.append({
                    'filename': meta.get('filename', 'Unknown'),
                    'page': meta.get('page', 'Unknown'),
                    'chunk_id': meta.get('chunk_id', 0),
                    'content_preview': doc[:200] + "..." if len(doc) > 200 else doc
                })

        return RAGResult(
            answer=answer.strip(),
            sources=sources,
            context_used=context,
            query=question,
            model_used=self.ollama_model
        )

    def _build_prompt(self, question: str, context: str) -> str:
        """
        Build prompt for the LLM.

        Args:
            question: User's question
            context: Retrieved context

        Returns:
            Formatted prompt
        """
        prompt = f"""You are a helpful assistant that answers questions based on provided context from documents.

Context from documents:
{context}

Question: {question}

Instructions:
- Answer the question based ONLY on the provided context
- If you mention specific information, cite which document it came from (e.g., "According to Document 1...")
- If the context doesn't contain enough information to answer the question, say so
- Be concise and accurate

Answer:"""

        return prompt

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the RAG system"""
        count = self.collection.count()

        # Get unique documents
        if count > 0:
            all_metadata = self.collection.get(include=['metadatas'])['metadatas']
            unique_files = set(m.get('filename', 'Unknown') for m in all_metadata)
        else:
            unique_files = set()

        return {
            'total_chunks': count,
            'unique_documents': len(unique_files),
            'document_names': list(unique_files),
            'ollama_model': self.ollama_model,
            'embedding_model': self.embeddings.model_name,
            'persist_directory': self.persist_directory
        }

    def clear_database(self):
        """Clear all documents from the vector database"""
        try:
            self.chroma_client.delete_collection(name="documents")
            self.collection = self.chroma_client.get_or_create_collection(
                name="documents",
                metadata={"description": "DeepSeek-OCR processed documents"}
            )
            return True
        except Exception as e:
            raise RuntimeError(f"Failed to clear database: {str(e)}")

    def check_ollama_connection(self) -> Tuple[bool, str]:
        """
        Check if Ollama is running and model is available.

        Returns:
            Tuple of (is_available, message)
        """
        try:
            # Check if Ollama is running
            models = self.llm.list_models()

            if not models:
                return False, "Ollama is running but no models are installed"

            # Check if selected model is available
            if self.ollama_model not in models:
                return False, f"Model '{self.ollama_model}' not found. Available: {', '.join(models[:5])}"

            return True, f"Ollama is ready with model '{self.ollama_model}'"

        except ConnectionError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Error checking Ollama: {str(e)}"


# Convenience functions
def create_rag_system(
    ollama_model: str = "llama3.2",
    embedding_model: str = "all-MiniLM-L6-v2"
) -> LocalRAGSystem:
    """
    Create a RAG system with sensible defaults.

    Args:
        ollama_model: Ollama model to use
        embedding_model: Embedding model to use

    Returns:
        Configured LocalRAGSystem
    """
    return LocalRAGSystem(
        ollama_model=ollama_model,
        embedding_model=embedding_model
    )


# Example usage
if __name__ == "__main__":
    # Create RAG system
    rag = create_rag_system(ollama_model="llama3.2")

    # Check Ollama connection
    is_available, message = rag.check_ollama_connection()
    print(f"Ollama status: {message}")

    if is_available:
        # Add sample documents
        texts = [
            "The company's revenue increased by 25% in Q4 2024, reaching $10 million.",
            "The new product launch is scheduled for March 2025."
        ]
        metadatas = [
            {"filename": "financial_report.pdf", "page": 1},
            {"filename": "product_roadmap.pdf", "page": 3}
        ]

        chunks_added = rag.add_documents(texts, metadatas)
        print(f"Added {chunks_added} chunks to the database")

        # Query the system
        result = rag.query("What was the revenue in Q4 2024?")
        print(f"\nQuestion: {result.query}")
        print(f"Answer: {result.answer}")
        print(f"\nSources: {len(result.sources)} documents")

        # Get stats
        stats = rag.get_stats()
        print(f"\nRAG Stats: {stats}")
