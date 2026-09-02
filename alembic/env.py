import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from app.config import settings
from app.models import Base

# Optional: pin alembic_version to a specific schema (e.g. when migrating an
# isolated test schema that shares a search_path with `public`, so alembic
# doesn't fall back to reading public.alembic_version and conclude there is
# nothing to do). Unset for normal runs - behaves exactly as before.
version_table_schema = os.environ.get("ALEMBIC_SCHEMA")

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
# set_main_option() stores the value in a ConfigParser section, which
# applies BasicInterpolation to '%' on every subsequent read - any literal
# '%' in DATABASE_URL (e.g. a percent-encoded ODBC driver name/password)
# then raises "invalid interpolation syntax" instead of configuring
# anything. Escaping '%' -> '%%' survives the round trip; the stored value
# comes back out exactly as it went in.
config.set_main_option("sqlalchemy.url",
                       settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


# SQL Server has no Postgres-style search_path - unqualified table DDL/DML
# lands in whatever schema the migrations here don't otherwise specify
# (normally the connecting login's default schema, e.g. dbo). When
# ALEMBIC_SCHEMA is set (isolated test runs - see tests/conftest.py),
# schema_translate_map redirects every unqualified table reference to that
# schema too, not just the alembic_version bookkeeping table - the portable
# SQLAlchemy equivalent of what the old Postgres search_path connection
# option did implicitly.
_schema_translate_map = ({None: version_table_schema}
                         if version_table_schema else None)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=version_table_schema,
        schema_translate_map=_schema_translate_map,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        if _schema_translate_map:
            connection = connection.execution_options(
                schema_translate_map=_schema_translate_map)
        context.configure(
            connection=connection, target_metadata=target_metadata,
            version_table_schema=version_table_schema,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
