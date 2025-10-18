#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Professional Embedding Generator for Inventory Items
Version 4.0 - Complete and Production Ready
"""

import os
import sys
import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
import numpy as np
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text, and_, or_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from tqdm import tqdm
import argparse
import time
import warnings
from typing import Optional, List, Dict, Any
from sqlalchemy import or_


# Suppress warnings
warnings.filterwarnings('ignore')

# Add project path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import configuration directly
from config_manager import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
from models import InventoryItem, Base, MTOEmbeddingCache

# ============================================================================
# CONFIGURATION
# ============================================================================
BATCH_SIZE = 50
MODEL_NAME = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
EMBEDDING_DIM = 384
MAX_TEXT_LENGTH = 512
MAX_RETRIES = 3
CONNECTION_TIMEOUT = 30

# Create logs directory
LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)

# Setup professional logging
log_file = LOG_DIR / f'embedding_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('EmbeddingGenerator')


# ============================================================================
# EMBEDDING GENERATOR CLASS
# ============================================================================

class ProfessionalEmbeddingGenerator:
    """Professional embedding generator with robust error handling"""

    def __init__(self):
        """Initialize the generator"""
        self.model = None
        self.engine = None
        self.Session = None
        self.connection_string = None

        # Statistics
        self.stats = {
            'start_time': None,
            'end_time': None,
            'total_items': 0,
            'items_to_process': 0,
            'processed': 0,
            'successful': 0,
            'failed': 0,
            'skipped': 0,
            'already_embedded': 0,
            'errors': []
        }

        # Cache for processed items
        self.processed_cache = set()

    def _create_connection_string(self) -> str:
        """Create PostgreSQL connection string"""
        from urllib.parse import quote_plus

        user = quote_plus(DB_USER.strip() if DB_USER else '')
        password = quote_plus(DB_PASSWORD.strip() if DB_PASSWORD else '')

        return f"postgresql+psycopg2://{user}:{password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    def initialize(self) -> bool:
        """Initialize model and database connection"""
        try:
            logger.info("=" * 70)
            logger.info("EMBEDDING GENERATOR INITIALIZATION")
            logger.info("=" * 70)

            # 1. Load the model
            logger.info(f"Loading model: {MODEL_NAME}")
            logger.info("This may take a few minutes on first run...")

            try:
                self.model = SentenceTransformer(MODEL_NAME)

                # Test the model
                test_texts = [
                    "Test embedding generation",
                    "تست تولید بردار",
                    "PIPE-CS-SCH40-2IN"
                ]

                for test_text in test_texts:
                    test_embedding = self.model.encode(test_text, show_progress_bar=False)
                    assert len(test_embedding) == EMBEDDING_DIM, \
                        f"Embedding dimension mismatch: {len(test_embedding)} != {EMBEDDING_DIM}"

                logger.info(f"✅ Model loaded successfully (dimension: {EMBEDDING_DIM})")

            except Exception as e:
                logger.error(f"❌ Failed to load model: {e}")
                logger.info("Try downloading the model manually first")
                return False

            # 2. Connect to database
            logger.info(f"Connecting to database: {DB_HOST}:{DB_PORT}/{DB_NAME}")

            self.connection_string = self._create_connection_string()

            # Create engine with connection pooling
            self.engine = create_engine(
                self.connection_string,
                poolclass=NullPool,  # No connection pooling for scripts
                echo=False,
                connect_args={
                    'connect_timeout': CONNECTION_TIMEOUT,
                    'options': '-c statement_timeout=60000'  # 60 seconds timeout
                }
            )

            # Create session factory
            self.Session = sessionmaker(bind=self.engine)

            # Test connection
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT version()")).fetchone()
                logger.info(f"✅ Connected to: PostgreSQL {result[0][:20]}...")

                # Check total items
                total_count = conn.execute(
                    text("SELECT COUNT(*) FROM inventory_items")
                ).scalar()

                embedded_count = conn.execute(
                    text("SELECT COUNT(*) FROM inventory_items WHERE embedding_vector IS NOT NULL")
                ).scalar()

                logger.info(f"📊 Database status:")
                logger.info(f"   Total items: {total_count:,}")
                logger.info(f"   Items with embeddings: {embedded_count:,}")
                logger.info(f"   Coverage: {(embedded_count / total_count * 100):.1f}%" if total_count > 0 else "N/A")

            logger.info("✅ Initialization completed successfully")
            logger.info("=" * 70)
            return True

        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    def _prepare_item_text(self, item: InventoryItem) -> Optional[str]:
        """Prepare text from inventory item for embedding"""
        try:
            # استفاده از متد get_embedding_text که در مدل تعریف شده
            text = item.get_embedding_text()

            if text and len(text.strip()) > 0:
                # محدود کردن طول متن
                if len(text) > MAX_TEXT_LENGTH:
                    text = text[:MAX_TEXT_LENGTH]
                return text

            return None

        except Exception as e:
            logger.error(f"Error preparing text for item {item.id}: {e}")
            return None

    def _save_embedding_batch(self, session, embeddings_data: List[Dict[str, Any]]) -> int:
        """Save batch of embeddings to database"""
        success_count = 0

        for data in embeddings_data:
            try:
                # Convert embedding to JSON string
                embedding_json = json.dumps(data['embedding'])

                # Update database
                result = session.execute(
                    text("""
                        UPDATE inventory_items 
                        SET embedding_vector = CAST(:embedding AS json),
                            embedding_text = :text,
                            embedding_updated_at = :updated_at
                        WHERE id = :id
                    """),
                    {
                        'id': data['id'],
                        'embedding': embedding_json,
                        'text': data['text'][:500],  # Limit text length
                        'updated_at': data['timestamp']
                    }
                )

                if result.rowcount > 0:
                    success_count += 1
                    self.stats['successful'] += 1
                    self.processed_cache.add(data['id'])
                else:
                    self.stats['failed'] += 1
                    logger.warning(f"No rows updated for item {data['id']}")

            except Exception as e:
                self.stats['failed'] += 1
                self.stats['errors'].append({
                    'item_id': data['id'],
                    'error': str(e)
                })
                logger.error(f"Failed to save embedding for item {data['id']}: {e}")

        return success_count

    def process_batch(self, items: List[InventoryItem], session) -> bool:
        """Process a batch of items"""
        try:
            # Prepare texts for embedding
            items_to_embed = []

            for item in items:
                # Skip if already processed in this session
                if item.id in self.processed_cache:
                    self.stats['already_embedded'] += 1
                    continue

                # Prepare text
                text = self._prepare_item_text(item)

                if text:
                    items_to_embed.append({
                        'id': item.id,
                        'text': text,
                        'item': item
                    })
                else:
                    self.stats['skipped'] += 1
                    logger.debug(f"Skipped item {item.id}: No valid text")

            if not items_to_embed:
                return True

            # Generate embeddings
            texts = [item['text'] for item in items_to_embed]

            logger.debug(f"Generating embeddings for {len(texts)} items...")

            embeddings = self.model.encode(
                texts,
                batch_size=min(32, len(texts)),
                show_progress_bar=False,
                normalize_embeddings=True,
                convert_to_numpy=True
            )

            # Prepare data for saving
            timestamp = datetime.now(timezone.utc)
            embeddings_data = []

            for item_data, embedding in zip(items_to_embed, embeddings):
                embeddings_data.append({
                    'id': item_data['id'],
                    'embedding': embedding.tolist(),
                    'text': item_data['text'],
                    'timestamp': timestamp
                })

            # Save to database
            success_count = self._save_embedding_batch(session, embeddings_data)

            # Commit transaction
            session.commit()

            self.stats['processed'] += len(items_to_embed)

            logger.debug(f"Batch completed: {success_count}/{len(items_to_embed)} successful")

            return True

        except Exception as e:
            logger.error(f"Batch processing error: {e}")
            session.rollback()
            return False

    def generate_embeddings(self, force_regenerate: bool = False, filter_empty: bool = True):
        """Generate embeddings for all items"""
        self.stats['start_time'] = datetime.now()

        session = self.Session()

        try:
            # Build query
            query = session.query(InventoryItem)

            # Filter conditions - تصحیح شده
            if not force_regenerate:
                query = query.filter(
                    InventoryItem.embedding_vector.is_(None)  # فقط استفاده از is_(None)
                )

            if filter_empty:
                query = query.filter(InventoryItem.available_qty > 0)

            # Get items
            items = query.all()

            self.stats['total_items'] = session.query(InventoryItem).count()
            self.stats['items_to_process'] = len(items)

            if not items:
                logger.info("✅ No items need processing!")
                return

            logger.info(f"📊 Found {len(items):,} items to process")

            # Process in batches with progress bar
            with tqdm(total=len(items), desc="Processing items", unit="item") as pbar:
                for i in range(0, len(items), BATCH_SIZE):
                    batch = items[i:i + BATCH_SIZE]
                    success = self.process_batch(batch, session)

                    if not success:
                        logger.warning(f"Batch {i // BATCH_SIZE + 1} failed, continuing...")

                    pbar.update(len(batch))

                    # Progress report every 10 batches
                    if (i // BATCH_SIZE + 1) % 10 == 0:
                        self._print_progress()

            self.stats['end_time'] = datetime.now()

        except Exception as e:
            logger.error(f"Critical error during embedding generation: {e}")
            raise
        finally:
            session.close()  # بجای Session.remove() از close استفاده کنیم

    def verify_embeddings(self, sample_size: int = 10):
            """Verify generated embeddings quality and consistency"""
            session = self.Session()

            try:
                logger.info("\n" + "=" * 70)
                logger.info("EMBEDDING VERIFICATION")
                logger.info("=" * 70)

                # Get total count
                total_with_embeddings = session.execute(
                    text("SELECT COUNT(*) FROM inventory_items WHERE embedding_vector IS NOT NULL")
                ).scalar()

                if total_with_embeddings == 0:
                    logger.warning("No embeddings found in database!")
                    return

                logger.info(f"Total items with embeddings: {total_with_embeddings:,}")

                # Get random samples
                samples = session.execute(
                    text("""
                        SELECT 
                            id, 
                            item_code, 
                            description,
                            embedding_text, 
                            embedding_vector,
                            embedding_updated_at
                        FROM inventory_items
                        WHERE embedding_vector IS NOT NULL
                        ORDER BY RANDOM()
                        LIMIT :limit
                    """),
                    {'limit': sample_size}
                ).fetchall()

                logger.info(f"\nVerifying {len(samples)} random samples:")
                logger.info("-" * 50)

                issues = []

                for idx, sample in enumerate(samples, 1):
                    try:
                        # Parse embedding
                        if isinstance(sample.embedding_vector, str):
                            embedding = json.loads(sample.embedding_vector)
                        elif isinstance(sample.embedding_vector, list):
                            embedding = sample.embedding_vector
                        else:
                            embedding = sample.embedding_vector

                        embedding_array = np.array(embedding)

                        # Verify dimensions
                        if len(embedding_array) != EMBEDDING_DIM:
                            issues.append(
                                f"Item {sample.id}: Wrong dimension {len(embedding_array)} != {EMBEDDING_DIM}")
                            logger.error(f"❌ Sample {idx} - {sample.item_code}: Dimension error")
                            continue

                        # Check normalization
                        norm = np.linalg.norm(embedding_array)
                        is_normalized = abs(norm - 1.0) < 0.01

                        # Check for zero vector
                        is_zero = np.allclose(embedding_array, 0)

                        # Check text presence
                        has_text = sample.embedding_text and len(sample.embedding_text.strip()) > 0

                        # Report
                        status = "✅" if (is_normalized and not is_zero and has_text) else "⚠️"

                        logger.info(f"{status} Sample {idx}:")
                        logger.info(f"   Item: {sample.item_code or 'N/A'}")
                        logger.info(f"   Description: {(sample.description or 'N/A')[:50]}...")
                        logger.info(
                            f"   Embedding text length: {len(sample.embedding_text) if sample.embedding_text else 0}")
                        logger.info(
                            f"   Vector norm: {norm:.4f} {'(normalized)' if is_normalized else '(NOT normalized)'}")
                        logger.info(f"   Zero vector: {'Yes ⚠️' if is_zero else 'No ✅'}")
                        logger.info(f"   Updated: {sample.embedding_updated_at}")

                        if not is_normalized:
                            issues.append(f"Item {sample.id}: Not normalized (norm={norm:.4f})")
                        if is_zero:
                            issues.append(f"Item {sample.id}: Zero vector")
                        if not has_text:
                            issues.append(f"Item {sample.id}: Missing embedding text")

                    except Exception as e:
                        logger.error(f"❌ Sample {idx} - Error verifying item {sample.id}: {e}")
                        issues.append(f"Item {sample.id}: Verification error - {str(e)}")

                # Summary
                logger.info("-" * 50)
                if issues:
                    logger.warning(f"Found {len(issues)} issues:")
                    for issue in issues:
                        logger.warning(f"  - {issue}")
                else:
                    logger.info("✅ All samples passed verification!")

                # Test similarity between random pairs
                logger.info("\n" + "-" * 50)
                logger.info("Testing similarity between random pairs:")

                if len(samples) >= 2:
                    for _ in range(min(3, len(samples) // 2)):
                        idx1, idx2 = np.random.choice(len(samples), 2, replace=False)
                        sample1, sample2 = samples[idx1], samples[idx2]

                        try:
                            if isinstance(sample1.embedding_vector, str):
                                emb1 = np.array(json.loads(sample1.embedding_vector))
                            else:
                                emb1 = np.array(sample1.embedding_vector)

                            if isinstance(sample2.embedding_vector, str):
                                emb2 = np.array(json.loads(sample2.embedding_vector))
                            else:
                                emb2 = np.array(sample2.embedding_vector)

                            # Calculate cosine similarity
                            similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))

                            logger.info(f"\nSimilarity test:")
                            logger.info(f"  Item 1: {sample1.item_code or 'N/A'}")
                            logger.info(f"  Item 2: {sample2.item_code or 'N/A'}")
                            logger.info(f"  Cosine similarity: {similarity:.4f}")

                            if similarity > 0.8:
                                logger.info(f"  → High similarity (potentially related items)")
                            elif similarity > 0.5:
                                logger.info(f"  → Moderate similarity")
                            else:
                                logger.info(f"  → Low similarity (different items)")

                        except Exception as e:
                            logger.error(f"Error calculating similarity: {e}")

                logger.info("=" * 70)

            except Exception as e:
                logger.error(f"Error during verification: {e}")
                import traceback
                logger.debug(traceback.format_exc())

            finally:
                session.close()

    def _save_stats_to_file(self):
            """Save statistics to a JSON file"""
            try:
                stats_file = LOG_DIR / f"embedding_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

                # Convert datetime objects to strings
                stats_copy = self.stats.copy()
                if stats_copy['start_time']:
                    stats_copy['start_time'] = stats_copy['start_time'].isoformat()
                if stats_copy['end_time']:
                    stats_copy['end_time'] = stats_copy['end_time'].isoformat()

                with open(stats_file, 'w', encoding='utf-8') as f:
                    json.dump(stats_copy, f, ensure_ascii=False, indent=2)

                logger.info(f"📊 Statistics saved to: {stats_file}")

            except Exception as e:
                logger.warning(f"Could not save stats to file: {e}")

    async def update_item_embedding(self, item_id: int, force: bool = False) -> bool:
        """Update embedding for a single inventory item"""
        try:
            async with self.session_maker() as session:
                # واکشی آیتم
                result = await session.execute(
                    select(InventoryItem).where(InventoryItem.id == item_id)
                )
                item = result.scalar_one_or_none()

                if not item:
                    logger.warning(f"Item {item_id} not found")
                    return False

                # آماده‌سازی متن
                text = self._prepare_item_text(item)
                if not text:
                    logger.warning(f"No text to embed for item {item_id}")
                    return False

                # بررسی نیاز به بروزرسانی
                if not force and item.embedding_text == text and item.embedding_updated_at:
                    logger.debug(f"Item {item_id} embedding is up to date")
                    return True

                # استفاده از کش یا تولید جدید
                embedding = await self.check_or_get_cache(text, session)
                if embedding is None:
                    return False

                # ذخیره در دیتابیس
                item.embedding_vector = embedding  # از کش میاد که قبلاً list شده
                item.embedding_text = text
                item.embedding_updated_at = datetime.now(timezone.utc)

                await session.commit()
                logger.info(f"Updated embedding for item {item_id}")
                return True

        except Exception as e:
            logger.error(f"Error updating embedding for item {item_id}: {e}")
            return False

    def _print_progress(self):
        """Print progress statistics"""
        if self.stats['items_to_process'] > 0:
            progress = (self.stats['processed'] / self.stats['items_to_process']) * 100
            logger.info(f"Progress: {progress:.1f}% | "
                        f"Processed: {self.stats['processed']:,} | "
                        f"Successful: {self.stats['successful']:,} | "
                        f"Failed: {self.stats['failed']:,} | "
                        f"Skipped: {self.stats['skipped']:,}")

    def print_summary(self):
        """Print final summary statistics"""
        logger.info("\n" + "=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)

        if self.stats['end_time'] and self.stats['start_time']:
            duration = self.stats['end_time'] - self.stats['start_time']
            logger.info(f"⏱️  Duration: {duration}")

        logger.info(f"📊 Statistics:")
        logger.info(f"   Total items in database: {self.stats['total_items']:,}")
        logger.info(f"   Items to process: {self.stats['items_to_process']:,}")
        logger.info(f"   Successfully processed: {self.stats['successful']:,}")
        logger.info(f"   Failed: {self.stats['failed']:,}")
        logger.info(f"   Skipped (no text): {self.stats['skipped']:,}")
        logger.info(f"   Already embedded: {self.stats['already_embedded']:,}")

        if self.stats['items_to_process'] > 0:
            success_rate = (self.stats['successful'] / self.stats['items_to_process']) * 100
            logger.info(f"   Success rate: {success_rate:.1f}%")

        if self.stats['errors']:
            logger.info(f"\n⚠️  Errors occurred for {len(self.stats['errors'])} items:")
            for error in self.stats['errors'][:5]:  # Show first 5 errors
                logger.info(f"   - Item {error['item_id']}: {error['error']}")
            if len(self.stats['errors']) > 5:
                logger.info(f"   ... and {len(self.stats['errors']) - 5} more errors")

    def save_report(self):
        """Save detailed report to file"""
        try:
            report_file = LOG_DIR / f'report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'

            report = {
                'timestamp': datetime.now().isoformat(),
                'model': MODEL_NAME,
                'embedding_dimension': EMBEDDING_DIM,
                'statistics': self.stats,
                'database': {
                    'host': DB_HOST,
                    'port': DB_PORT,
                    'database': DB_NAME
                }
            }

            # Convert datetime objects to strings
            if self.stats['start_time']:
                report['statistics']['start_time'] = self.stats['start_time'].isoformat()
            if self.stats['end_time']:
                report['statistics']['end_time'] = self.stats['end_time'].isoformat()

            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            logger.info(f"📄 Report saved to: {report_file}")

        except Exception as e:
            logger.error(f"Failed to save report: {e}")

    def check_or_get_cache(self, text: str, session) -> Optional[List[float]]:
        """Check cache or generate new embedding"""
        try:
            # محاسبه هش متن
            text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()

            # جستجو در کش
            cache_entry = session.query(MTOEmbeddingCache).filter(
                MTOEmbeddingCache.text_hash == text_hash
            ).first()

            if cache_entry and cache_entry.embedding_vector:
                logger.debug(f"Cache hit for hash {text_hash[:8]}...")
                return cache_entry.embedding_vector

            # تولید embedding جدید
            logger.debug(f"Cache miss, generating new embedding for hash {text_hash[:8]}...")
            embedding = self.model.encode(text, show_progress_bar=False)
            embedding_list = embedding.tolist()

            # ذخیره در کش
            if not cache_entry:
                cache_entry = MTOEmbeddingCache(
                    text_hash=text_hash,
                    text_content=text[:1000],  # ذخیره بخشی از متن برای رفرنس
                    embedding_vector=embedding_list,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(cache_entry)
            else:
                cache_entry.embedding_vector = embedding_list
                cache_entry.updated_at = datetime.now(timezone.utc)

            session.commit()
            return embedding_list

        except Exception as e:
            logger.error(f"Cache operation failed: {e}")
            # در صورت خطا، مستقیم embedding تولید کن
            embedding = self.model.encode(text, show_progress_bar=False)
            return embedding.tolist()

    # ============================================================================
    # MAIN ENTRY POINT
    # ============================================================================


def main():
    """Main entry point for the script"""

    parser = argparse.ArgumentParser(
        description='Professional Embedding Generator for Inventory Items',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_embeddings.py                    # Generate embeddings for new items only
  python generate_embeddings.py --force-all        # Regenerate all embeddings
  python generate_embeddings.py --verify           # Generate and then verify
  python generate_embeddings.py --verify-only      # Only verify existing embeddings
        """
    )

    parser.add_argument(
        '--force-all',
        action='store_true',
        help='Force regeneration of all embeddings (ignore existing ones)'
    )

    parser.add_argument(
        '--verify',
        action='store_true',
        help='Verify embeddings after generation'
    )

    parser.add_argument(
        '--verify-only',
        action='store_true',
        help='Only verify existing embeddings without generating new ones'
    )

    parser.add_argument(
        '--sample-size',
        type=int,
        default=10,
        help='Number of samples to verify (default: 10)'
    )

    parser.add_argument(
        '--include-empty',
        action='store_true',
        help='Include items with zero quantity'
    )

    args = parser.parse_args()

    # Print header
    logger.info("=" * 70)
    logger.info("INVENTORY EMBEDDING GENERATOR v4.0")
    logger.info("=" * 70)
    logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Model: {MODEL_NAME}")
    logger.info(f"Embedding dimension: {EMBEDDING_DIM}")
    logger.info(f"Batch size: {BATCH_SIZE}")
    logger.info("=" * 70)

    # Initialize generator
    generator = ProfessionalEmbeddingGenerator()

    if not generator.initialize():
        logger.error("❌ Failed to initialize generator. Exiting.")
        sys.exit(1)

    try:
        # Run based on arguments
        if args.verify_only:
            logger.info("Running verification only...")
            generator.verify_embeddings(sample_size=args.sample_size)
        else:
            # Generate embeddings
            logger.info("Starting embedding generation...")
            logger.info("Options:")
            logger.info(f"  - Force regenerate all: {args.force_all}")
            logger.info(f"  - Include empty items: {args.include_empty}")
            logger.info(f"  - Verify after generation: {args.verify}")
            logger.info("")

            generator.generate_embeddings(
                force_regenerate=args.force_all,
                filter_empty=not args.include_empty
            )

            # Print final statistics
            generator.print_summary()

            # Save report
            generator.save_report()

            # Verify if requested
            if args.verify:
                logger.info("\n" + "=" * 70)
                logger.info("VERIFICATION")
                logger.info("=" * 70)
                generator.verify_embeddings(sample_size=args.sample_size)

    except KeyboardInterrupt:
        logger.warning("\n⚠️  Process interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        sys.exit(1)
    finally:
        # Cleanup - حذف Session.remove()
        if generator.engine:
            generator.engine.dispose()
            logger.info("Database connections closed")



if __name__ == "__main__":  # تغییر از "__main_" به "__main__"
    main()
