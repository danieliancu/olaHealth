"""Apply Open Food Facts daily deltas to the local catalogue (catalog/services/delta.py).

python manage.py sync_off_delta                    # every file still to do, oldest first
python manage.py sync_off_delta --max-files 1      # only the next one
python manage.py sync_off_delta --file openfoodfacts_products_<from>_<to>.json.gz
python manage.py sync_off_delta --plan             # what would be processed, nothing else
python manage.py sync_off_delta --reset-start      # start point again from the archive
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from catalog.services import delta


class Command(BaseCommand):
    help = "Apply Open Food Facts daily delta files to the local catalogue."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--file", default="", help="Process this delta file only")
        parser.add_argument("--max-files", type=int, default=None)
        parser.add_argument("--since", type=int, default=None, help="Start point (UTC seconds)")
        parser.add_argument("--reset-start", action="store_true")
        parser.add_argument("--keep-files", action="store_true", help="Keep downloaded files")
        parser.add_argument("--plan", action="store_true", help="Show the plan and stop")
        parser.add_argument(
            "--stop-after", type=int, default=None, help="Stop after N lines (interruption test)"
        )

    def handle(self, *args, **options) -> None:
        if options["plan"]:
            self._plan()
            return
        try:
            report = delta.run(
                only=options["file"] or None,
                max_files=options["max_files"],
                since=options["since"],
                reset_start=options["reset_start"],
                keep_files=options["keep_files"],
                stop_after=options["stop_after"],
            )
        except delta.Locked as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        if report.get("status") != "ok":
            raise CommandError(report.get("stopped") or "delta sync failed")

    def _plan(self) -> None:
        from catalog.models import OffDeltaState

        state = OffDeltaState.load()
        start = state.start_from
        note = state.start_note
        if start is None:
            start, note = delta.provenance()
        if start is None:
            raise CommandError(note)
        state.start_from = start
        with delta._client() as client:
            refs = delta.fetch_index(client)
        todo, gap = delta.plan(refs, state)
        self.stdout.write(f"Start from {delta.ts(start)} ({note})")
        self.stdout.write(f"Policy: {delta.policy()['new_products']} for new products")
        for r in todo:
            self.stdout.write(f"  {r.name}  {delta.ts(r.start_ts)} → {delta.ts(r.end_ts)}")
        if gap:
            self.stdout.write(f"GAP: {gap}")
