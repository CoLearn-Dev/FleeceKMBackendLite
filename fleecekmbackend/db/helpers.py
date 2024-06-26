import random
from fleecekmbackend.db.ctl import async_session, engine
from fleecekmbackend.db.models import Paragraph, Author
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
import pandas as pd
import logging
from tqdm import tqdm


async def load_csv_data(file):
    async with async_session() as db:
        try:
            async with engine.connect() as conn:
                table_exists = await conn.run_sync(
                    lambda sync_conn: sync_conn.dialect.has_table(
                        sync_conn, Paragraph.__tablename__
                    )
                )
                if table_exists:
                    result = await conn.execute(
                        select(func.max(Paragraph.id)).select_from(Paragraph.__table__)
                    )
                    count = result.scalar()
                    if count and count > 0:
                        logging.info(
                            f"Dataset is already loaded with {count} entries. Skipping loading process."
                        )
                        return
            df = pd.read_csv(file)
            df["within_page_order"] = df.groupby("page_name").cumcount()
            df = df.where(pd.notnull(df), None)

            if not table_exists:
                async with engine.begin() as conn:
                    await conn.run_sync(Paragraph.__table__.create)
            async with engine.begin() as conn:
                await conn.execute(text("SET SESSION sql_mode='NO_AUTO_VALUE_ON_ZERO'"))

                for _, row in tqdm(df.iterrows(), total=len(df), desc="Inserting data"):
                    await conn.execute(
                        Paragraph.__table__.insert().values(row.to_dict())
                    )
        except Exception as e:
            logging.error(f"Error loading CSV data: {str(e)}")
            await conn.rollback()
        finally:
            logging.info("Data loading completed.")


async def get_random_samples_raw(n: int, db: AsyncSession):
    query = select(Paragraph).order_by(func.random()).limit(n)
    result = await db.execute(query)
    samples = result.scalars().all()
    return samples


async def get_random_samples_raw_as_df(n: int, db: AsyncSession):
    query = select(Paragraph).order_by(func.random()).limit(n)
    result = await db.execute(query)
    samples = result.scalars().all()
    df = pd.DataFrame([sample.__dict__ for sample in samples])
    df = df.drop(columns=["_sa_instance_state"])
    return df


async def get_random_unprocessed_paragraphs(db: AsyncSession, n: int = 1):
    try:
        paragraphs = []
        while not paragraphs:
            max_processed = (
                await db.execute(
                    select(func.count(Paragraph.id)).where(Paragraph.processed != -1)
                )
            ).scalar()
            total_paragraphs = (
                await db.execute(select(func.max(Paragraph.id)))
            ).scalar()
            offset = random.randint(0, int(total_paragraphs) - int(max_processed))
            paragraphs = (
                await db.execute(
                    select(Paragraph)
                    .where(Paragraph.processed == -1)
                    .offset(offset)
                    .limit(n)
                )
            ).scalar()
        if not paragraphs:
            raise Exception("No unprocessed paragraphs found")
        elif isinstance(paragraphs, Paragraph):
            return [paragraphs]
        return paragraphs
    except Exception as e:
        logging.error(f"Error retrieving random unprocessed paragraph: {str(e)}")
        return -1


async def get_next_unprocessed_paragraphs(db: AsyncSession, n: int = 1):
    try:
        query = (
            select(Paragraph)
            .filter(Paragraph.processed == False)
            .limit(n)
            .with_for_update(skip_locked=True)
        )
        result = await db.execute(query)
        paragraphs = result.scalars().all()
        if not paragraphs:
            raise Exception("No unprocessed paragraphs found")
        return paragraphs

    except Exception as e:
        await db.rollback()
        logging.error(f"Error retrieving next unprocessed paragraphs: {str(e)}")
        return []


async def get_page_raw(db: AsyncSession, index: int = -1):
    if (
        index == -1
    ):  # get all the paragraphs with the same (randomly selected) page_name
        query = select(Paragraph.page_name).distinct().order_by(func.random()).limit(1)
        result = await db.execute(query)
        page_name = result.scalar()
        query = select(Paragraph).filter(Paragraph.page_name == page_name)
    else:  # get paragraphs with pagename in order
        query = (
            select(Paragraph.page_name)
            .distinct()
            .order_by(Paragraph.page_name)
            .offset(index)
            .limit(1)
        )
        result = await db.execute(query)
        page_name = result.scalar()
        query = select(Paragraph).filter(Paragraph.page_name == page_name)
    result = await db.execute(query)
    samples = result.scalars().all()
    return samples


async def create_author_if_not_exists(prompt: str, model: str):
    async with async_session() as db:
        result = await db.execute(
            select(Author).filter(Author.model == model, Author.prompt == prompt)
        )
        author = result.scalar()
        if author is None:
            author = Author(model=model, prompt=prompt)
            db.add(author)
            await db.commit()
            await db.refresh(author, ["id"])
        author_id = author.id
        return author_id
