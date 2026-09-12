import pymysql

# Django's MySQL backend imports MySQLdb (mysqlclient). Replace it with PyMySQL, the pure-Python driver.
pymysql.install_as_MySQLdb()

# Load the Celery app when Django starts up so @shared_task binds to this app.
from config.celery import app as celery_app  # noqa: E402

__all__ = ["celery_app"]
