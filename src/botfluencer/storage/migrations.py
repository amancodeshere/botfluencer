from pathlib import Path

from alembic import command
from alembic.config import Config

from botfluencer.config import Settings


def migration_config(settings: Settings) -> Config:
    root = Path(__file__).resolve().parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(root))
    config.attributes["database_url"] = settings.sqlalchemy_url()
    return config


def upgrade(settings: Settings) -> None:
    command.upgrade(migration_config(settings), "head")
