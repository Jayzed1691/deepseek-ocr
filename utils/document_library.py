"""
Document Library System
Persistent document management with tagging, collections, and organization
"""

import sqlite3
import json
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
import hashlib


@dataclass
class Document:
    """Document metadata"""
    doc_id: str
    filename: str
    file_type: str
    page_count: int
    upload_date: str
    processing_method: str  # 'text', 'ocr', 'hybrid'
    char_count: int
    indexed: bool = True
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    collection: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Document':
        """Create from dictionary"""
        if 'tags' in data and isinstance(data['tags'], str):
            data['tags'] = json.loads(data['tags'])
        return cls(**data)


@dataclass
class Collection:
    """Document collection/project"""
    collection_id: str
    name: str
    description: str
    created_date: str
    doc_count: int = 0
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)


@dataclass
class LibraryStats:
    """Library statistics"""
    total_documents: int
    total_pages: int
    total_chars: int
    indexed_documents: int
    collections_count: int
    unique_tags: int
    storage_size_mb: float
    oldest_document: Optional[str]
    newest_document: Optional[str]


class DocumentLibrary:
    """
    Document Library with persistent storage and organization.

    Features:
    - Document metadata storage
    - Tagging system
    - Collections/projects
    - Search and filtering
    - Import/export
    """

    def __init__(self, db_path: str = "./document_library.db"):
        """
        Initialize document library.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Documents table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_type TEXT,
                page_count INTEGER,
                upload_date TEXT,
                processing_method TEXT,
                char_count INTEGER,
                indexed BOOLEAN,
                tags TEXT,
                notes TEXT,
                collection TEXT
            )
        """)

        # Collections table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                collection_id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_date TEXT,
                tags TEXT
            )
        """)

        # Tags table (for tag statistics)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                tag_name TEXT PRIMARY KEY,
                usage_count INTEGER DEFAULT 0,
                created_date TEXT
            )
        """)

        # Search history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_history (
                search_id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT,
                timestamp TEXT,
                results_count INTEGER
            )
        """)

        conn.commit()
        conn.close()

    def add_document(
        self,
        filename: str,
        file_type: str,
        page_count: int,
        processing_method: str,
        char_count: int,
        tags: Optional[List[str]] = None,
        notes: str = "",
        collection: Optional[str] = None
    ) -> str:
        """
        Add document to library.

        Args:
            filename: Document filename
            file_type: File type (pdf, etc.)
            page_count: Number of pages
            processing_method: How it was processed (text/ocr/hybrid)
            char_count: Character count
            tags: Optional tags
            notes: Optional notes
            collection: Optional collection name

        Returns:
            Document ID
        """
        # Generate document ID
        doc_id = hashlib.md5(
            f"{filename}{datetime.now().isoformat()}".encode()
        ).hexdigest()[:16]

        doc = Document(
            doc_id=doc_id,
            filename=filename,
            file_type=file_type,
            page_count=page_count,
            upload_date=datetime.now().isoformat(),
            processing_method=processing_method,
            char_count=char_count,
            indexed=True,
            tags=tags or [],
            notes=notes,
            collection=collection
        )

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO documents
            (doc_id, filename, file_type, page_count, upload_date,
             processing_method, char_count, indexed, tags, notes, collection)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            doc.doc_id, doc.filename, doc.file_type, doc.page_count,
            doc.upload_date, doc.processing_method, doc.char_count,
            doc.indexed, json.dumps(doc.tags), doc.notes, doc.collection
        ))

        # Update tags
        if tags:
            for tag in tags:
                self._update_tag_count(cursor, tag, 1)

        # Update collection doc count
        if collection:
            self._update_collection_doc_count(cursor, collection, 1)

        conn.commit()
        conn.close()

        return doc_id

    def get_document(self, doc_id: str) -> Optional[Document]:
        """Get document by ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM documents WHERE doc_id = ?
        """, (doc_id,))

        row = cursor.fetchone()
        conn.close()

        if row:
            return Document(
                doc_id=row[0],
                filename=row[1],
                file_type=row[2],
                page_count=row[3],
                upload_date=row[4],
                processing_method=row[5],
                char_count=row[6],
                indexed=bool(row[7]),
                tags=json.loads(row[8]) if row[8] else [],
                notes=row[9] or "",
                collection=row[10]
            )
        return None

    def list_documents(
        self,
        collection: Optional[str] = None,
        tags: Optional[List[str]] = None,
        search_query: Optional[str] = None,
        sort_by: str = "upload_date",
        ascending: bool = False
    ) -> List[Document]:
        """
        List documents with filtering and sorting.

        Args:
            collection: Filter by collection
            tags: Filter by tags (any match)
            search_query: Search in filename/notes
            sort_by: Sort field
            ascending: Sort order

        Returns:
            List of documents
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        query = "SELECT * FROM documents WHERE 1=1"
        params = []

        if collection:
            query += " AND collection = ?"
            params.append(collection)

        if search_query:
            query += " AND (filename LIKE ? OR notes LIKE ?)"
            search_pattern = f"%{search_query}%"
            params.extend([search_pattern, search_pattern])

        # Sorting
        order = "ASC" if ascending else "DESC"
        query += f" ORDER BY {sort_by} {order}"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        documents = []
        for row in rows:
            doc = Document(
                doc_id=row[0],
                filename=row[1],
                file_type=row[2],
                page_count=row[3],
                upload_date=row[4],
                processing_method=row[5],
                char_count=row[6],
                indexed=bool(row[7]),
                tags=json.loads(row[8]) if row[8] else [],
                notes=row[9] or "",
                collection=row[10]
            )

            # Filter by tags if specified
            if tags:
                if any(tag in doc.tags for tag in tags):
                    documents.append(doc)
            else:
                documents.append(doc)

        return documents

    def update_document(
        self,
        doc_id: str,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
        collection: Optional[str] = None
    ) -> bool:
        """Update document metadata"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get current document
        cursor.execute("SELECT tags, collection FROM documents WHERE doc_id = ?", (doc_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False

        old_tags = json.loads(row[0]) if row[0] else []
        old_collection = row[1]

        updates = []
        params = []

        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags))

            # Update tag counts
            for tag in old_tags:
                if tag not in tags:
                    self._update_tag_count(cursor, tag, -1)
            for tag in tags:
                if tag not in old_tags:
                    self._update_tag_count(cursor, tag, 1)

        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)

        if collection is not None:
            updates.append("collection = ?")
            params.append(collection)

            # Update collection counts
            if old_collection:
                self._update_collection_doc_count(cursor, old_collection, -1)
            if collection:
                self._update_collection_doc_count(cursor, collection, 1)

        if updates:
            params.append(doc_id)
            cursor.execute(
                f"UPDATE documents SET {', '.join(updates)} WHERE doc_id = ?",
                params
            )

        conn.commit()
        conn.close()
        return True

    def delete_document(self, doc_id: str) -> bool:
        """Delete document from library"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get document info
        cursor.execute("SELECT tags, collection FROM documents WHERE doc_id = ?", (doc_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False

        tags = json.loads(row[0]) if row[0] else []
        collection = row[1]

        # Update tag counts
        for tag in tags:
            self._update_tag_count(cursor, tag, -1)

        # Update collection count
        if collection:
            self._update_collection_doc_count(cursor, collection, -1)

        # Delete document
        cursor.execute("DELETE FROM documents WHERE doc_id = ?", (doc_id,))

        conn.commit()
        conn.close()
        return True

    def create_collection(
        self,
        name: str,
        description: str = "",
        tags: Optional[List[str]] = None
    ) -> str:
        """Create a new collection"""
        collection_id = hashlib.md5(
            f"{name}{datetime.now().isoformat()}".encode()
        ).hexdigest()[:16]

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO collections (collection_id, name, description, created_date, tags)
                VALUES (?, ?, ?, ?, ?)
            """, (
                collection_id, name, description,
                datetime.now().isoformat(),
                json.dumps(tags or [])
            ))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            raise ValueError(f"Collection '{name}' already exists")

        conn.close()
        return collection_id

    def list_collections(self) -> List[Collection]:
        """List all collections"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT c.*, COUNT(d.doc_id) as doc_count
            FROM collections c
            LEFT JOIN documents d ON c.name = d.collection
            GROUP BY c.collection_id
        """)

        rows = cursor.fetchall()
        conn.close()

        collections = []
        for row in rows:
            collections.append(Collection(
                collection_id=row[0],
                name=row[1],
                description=row[2] or "",
                created_date=row[3],
                tags=json.loads(row[4]) if row[4] else [],
                doc_count=row[5]
            ))

        return collections

    def delete_collection(self, collection_id: str, delete_documents: bool = False) -> bool:
        """
        Delete collection.

        Args:
            collection_id: Collection to delete
            delete_documents: If True, delete all documents in collection

        Returns:
            Success status
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Get collection name
        cursor.execute("SELECT name FROM collections WHERE collection_id = ?", (collection_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False

        collection_name = row[0]

        if delete_documents:
            # Delete all documents in collection
            cursor.execute("DELETE FROM documents WHERE collection = ?", (collection_name,))
        else:
            # Just remove collection reference
            cursor.execute("UPDATE documents SET collection = NULL WHERE collection = ?", (collection_name,))

        # Delete collection
        cursor.execute("DELETE FROM collections WHERE collection_id = ?", (collection_id,))

        conn.commit()
        conn.close()
        return True

    def get_all_tags(self) -> List[Tuple[str, int]]:
        """Get all tags with usage counts"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT tag_name, usage_count
            FROM tags
            WHERE usage_count > 0
            ORDER BY usage_count DESC
        """)

        tags = cursor.fetchall()
        conn.close()
        return tags

    def get_stats(self) -> LibraryStats:
        """Get library statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Total documents
        cursor.execute("SELECT COUNT(*) FROM documents")
        total_docs = cursor.fetchone()[0]

        # Total pages and chars
        cursor.execute("SELECT SUM(page_count), SUM(char_count) FROM documents")
        row = cursor.fetchone()
        total_pages = row[0] or 0
        total_chars = row[1] or 0

        # Indexed documents
        cursor.execute("SELECT COUNT(*) FROM documents WHERE indexed = 1")
        indexed_docs = cursor.fetchone()[0]

        # Collections count
        cursor.execute("SELECT COUNT(*) FROM collections")
        collections_count = cursor.fetchone()[0]

        # Unique tags
        cursor.execute("SELECT COUNT(*) FROM tags WHERE usage_count > 0")
        unique_tags = cursor.fetchone()[0]

        # Date range
        cursor.execute("SELECT MIN(upload_date), MAX(upload_date) FROM documents")
        row = cursor.fetchone()
        oldest = row[0]
        newest = row[1]

        conn.close()

        # Storage size (rough estimate)
        try:
            db_size = Path(self.db_path).stat().st_size / (1024 * 1024)  # MB
        except:
            db_size = 0.0

        return LibraryStats(
            total_documents=total_docs,
            total_pages=total_pages,
            total_chars=total_chars,
            indexed_documents=indexed_docs,
            collections_count=collections_count,
            unique_tags=unique_tags,
            storage_size_mb=db_size,
            oldest_document=oldest,
            newest_document=newest
        )

    def export_library(self, output_path: str) -> bool:
        """Export entire library to JSON"""
        try:
            documents = self.list_documents()
            collections = self.list_collections()
            tags = self.get_all_tags()

            export_data = {
                'export_date': datetime.now().isoformat(),
                'documents': [doc.to_dict() for doc in documents],
                'collections': [coll.to_dict() for coll in collections],
                'tags': [{'name': name, 'count': count} for name, count in tags],
                'stats': asdict(self.get_stats())
            }

            with open(output_path, 'w') as f:
                json.dump(export_data, f, indent=2)

            return True
        except Exception as e:
            print(f"Export failed: {e}")
            return False

    def import_library(self, input_path: str, merge: bool = True) -> bool:
        """
        Import library from JSON.

        Args:
            input_path: Path to JSON file
            merge: If True, merge with existing data. If False, clear first.

        Returns:
            Success status
        """
        try:
            with open(input_path, 'r') as f:
                import_data = json.load(f)

            if not merge:
                # Clear existing data
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM documents")
                cursor.execute("DELETE FROM collections")
                cursor.execute("DELETE FROM tags")
                conn.commit()
                conn.close()

            # Import collections first
            for coll_data in import_data.get('collections', []):
                try:
                    self.create_collection(
                        name=coll_data['name'],
                        description=coll_data.get('description', ''),
                        tags=coll_data.get('tags', [])
                    )
                except ValueError:
                    # Collection already exists
                    pass

            # Import documents
            for doc_data in import_data.get('documents', []):
                # Check if document already exists
                if not self.get_document(doc_data['doc_id']):
                    self.add_document(
                        filename=doc_data['filename'],
                        file_type=doc_data['file_type'],
                        page_count=doc_data['page_count'],
                        processing_method=doc_data['processing_method'],
                        char_count=doc_data['char_count'],
                        tags=doc_data.get('tags', []),
                        notes=doc_data.get('notes', ''),
                        collection=doc_data.get('collection')
                    )

            return True
        except Exception as e:
            print(f"Import failed: {e}")
            return False

    def _update_tag_count(self, cursor, tag: str, delta: int):
        """Update tag usage count"""
        cursor.execute("""
            INSERT INTO tags (tag_name, usage_count, created_date)
            VALUES (?, ?, ?)
            ON CONFLICT(tag_name) DO UPDATE SET usage_count = usage_count + ?
        """, (tag, delta, datetime.now().isoformat(), delta))

    def _update_collection_doc_count(self, cursor, collection: str, delta: int):
        """Update collection document count"""
        # This is implicitly handled by COUNT in list_collections
        # But we could cache it if needed
        pass


# Example usage
if __name__ == "__main__":
    library = DocumentLibrary()

    # Add a document
    doc_id = library.add_document(
        filename="report.pdf",
        file_type="pdf",
        page_count=50,
        processing_method="hybrid",
        char_count=50000,
        tags=["finance", "Q4"],
        notes="Important financial report"
    )
    print(f"Added document: {doc_id}")

    # Create collection
    coll_id = library.create_collection(
        name="Financial Reports",
        description="All financial documents",
        tags=["finance", "reports"]
    )
    print(f"Created collection: {coll_id}")

    # Update document
    library.update_document(doc_id, collection="Financial Reports")

    # List documents
    docs = library.list_documents()
    print(f"Total documents: {len(docs)}")

    # Get stats
    stats = library.get_stats()
    print(f"Library stats: {asdict(stats)}")
