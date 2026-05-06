from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sub" ADD "ai_tags" SMALLINT NOT NULL  DEFAULT -100;
        ALTER TABLE "user" ADD "ai_tags" SMALLINT NOT NULL  DEFAULT 0;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "sub" DROP COLUMN "ai_tags";
        ALTER TABLE "user" DROP COLUMN "ai_tags";"""
