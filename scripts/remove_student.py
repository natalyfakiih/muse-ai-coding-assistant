"""Remove one student's work, or the student entirely.

    python -m scripts.remove_student "Jad A."             # show what would go
    python -m scripts.remove_student "Jad A." --confirm   # actually remove it
    python -m scripts.remove_student "Jad A." --keep-student --confirm

Dry run by default. Nothing is written until --confirm is passed, because this
cannot be undone: MUSE keeps no tombstones, and a student's specification
history is the only record of how they worked.

Deletion order matters. Interaction carries a foreign key to DiffResult, and
both hang off Specification, so children go first and the student row last. A
hand-written DELETE in the wrong order either fails or silently orphans rows.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.models import (
    CodeSnapshot,
    DiffResult,
    GateTurn,
    Interaction,
    Specification,
    Student,
)


def find(db: Session, who: str) -> Student:
    """By id if it looks like one, otherwise by name, case-insensitively."""
    if who.isdigit():
        student = db.get(Student, int(who))
        if student is None:
            raise SystemExit(f"No student with id {who}.")
        return student

    matches = db.scalars(
        select(Student).where(Student.name.ilike(f"%{who}%")).order_by(Student.id)
    ).all()
    if not matches:
        known = ", ".join(s.name for s in db.scalars(select(Student).order_by(Student.id)).all())
        raise SystemExit(f"No student matching {who!r}.\nIn this course: {known}")
    if len(matches) > 1:
        listed = "\n".join(f"  {s.id}  {s.name}" for s in matches)
        raise SystemExit(f"{who!r} matches more than one student. Re-run with the id:\n{listed}")
    return matches[0]


def tally(db: Session, student: Student) -> dict[str, list]:
    """Everything that belongs to this student, grouped by table."""
    specs = db.scalars(
        select(Specification).where(Specification.student_id == student.id)
    ).all()
    spec_ids = [s.id for s in specs]

    def owned(model, column):
        if not spec_ids:
            return []
        return db.scalars(select(model).where(column.in_(spec_ids))).all()

    return {
        # listed in deletion order: children first
        "interactions": owned(Interaction, Interaction.specification_id),
        "diff_results": owned(DiffResult, DiffResult.specification_id),
        "code_snapshots": owned(CodeSnapshot, CodeSnapshot.specification_id),
        "gate_turns": db.scalars(
            select(GateTurn).where(GateTurn.student_id == student.id)).all(),
        "specifications": specs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("who", help="student name (or part of it), or a numeric id")
    parser.add_argument("--confirm", action="store_true",
                        help="actually delete. Without this, nothing is written.")
    parser.add_argument("--keep-student", action="store_true",
                        help="wipe their work but leave them on the roster")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        student = find(db, args.who)
        rows = tally(db, student)
        total = sum(len(v) for v in rows.values())

        print(f"{student.name}  (id {student.id}, cohort {student.cohort or 'none'})")
        for table, items in rows.items():
            print(f"  {table:<16} {len(items)}")
        print(f"  {'student row':<16} {0 if args.keep_student else 1}")
        print(f"  {'':<16} {'-' * 4}\n  {'total':<16} {total + (0 if args.keep_student else 1)}")

        if not args.confirm:
            print("\nDry run, nothing was deleted.")
            print("Re-run with --confirm to remove it. This cannot be undone, so copy")
            print("muse.db somewhere first if you might want it back.")
            return

        for items in rows.values():
            for row in items:
                db.delete(row)
            db.flush()  # children committed before the next table references them
        if not args.keep_student:
            db.delete(student)
        db.commit()

        if args.keep_student:
            print(f"\nRemoved {total} row(s). {student.name} is still on the roster "
                  f"with no work recorded.")
        else:
            print(f"\nRemoved {total + 1} row(s), including {student.name}.")
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
