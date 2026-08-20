import asyncio
from app.database import engine, Base

# We must import the models here so SQLAlchemy's Base knows they exist
from app.models.document import Document 
# If you have a Chat or Message model, import them here too, e.g.:
# from app.models.chat import Chat

async def init_models():
    print("Creating database tables...")
    async with engine.begin() as conn:
        # This will create all tables defined by your models that don't already exist
        await conn.run_sync(Base.metadata.create_all)
    print("Successfully created tables!")

if __name__ == "__main__":
    asyncio.run(init_models())