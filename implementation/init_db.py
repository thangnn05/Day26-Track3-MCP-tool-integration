"""Create and seed the SQLite database used by the MCP server.

Run this once before starting the server, or re-run any time to reset the
database. The script is idempotent: it drops and recreates the demo tables.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent / "lab.db"


SCHEMA_SQL = """
DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS students;

CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cohort TEXT NOT NULL,
    email TEXT UNIQUE,
    score REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    credits INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    course_id INTEGER NOT NULL REFERENCES courses(id),
    grade REAL,
    UNIQUE(student_id, course_id)
);
"""

SEED_SQL = """
INSERT INTO students (name, cohort, email, score) VALUES
    ('Ava Tran',    'A1', 'ava@example.com',    91.5),
    ('Bao Nguyen',  'A1', 'bao@example.com',    78.0),
    ('Chau Le',     'A1', 'chau@example.com',   84.2),
    ('Duc Pham',    'B2', 'duc@example.com',    66.0),
    ('Emi Hoang',   'B2', 'emi@example.com',    72.5),
    ('Felix Vu',    'C3', 'felix@example.com',  95.0);

INSERT INTO courses (code, title, credits) VALUES
    ('CS101', 'Intro to Programming', 3),
    ('CS201', 'Data Structures',      4),
    ('MA110', 'Calculus I',           4),
    ('DB220', 'Databases',            3);

INSERT INTO enrollments (student_id, course_id, grade) VALUES
    (1, 1, 9.0),
    (1, 2, 8.5),
    (2, 1, 7.0),
    (2, 3, 6.5),
    (3, 2, 8.0),
    (3, 4, 9.2),
    (4, 1, 5.5),
    (5, 3, 7.8),
    (6, 4, 9.8);
"""


def create_database(db_path: str | Path = DEFAULT_DB_PATH) -> Path:
    """Create the schema and seed data. Returns the database path."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA_SQL)
        conn.executescript(SEED_SQL)
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    target = os.environ.get("MCP_LAB_DB", str(DEFAULT_DB_PATH))
    written = create_database(target)
    print(f"Initialized database at {written}")
