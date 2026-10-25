from django.core.management.base import BaseCommand
import os
from hotspots.data_pipeline import import_observations_from_csv
from hotspots.models import Observation


class Command(BaseCommand):
    help = "Import satellite observations from CSV with validation and explainable priority scoring"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            default=os.path.join("hotspots", "data", "simulated_benchmark_hotspots.csv"),
            help="Path to the CSV file to import (default: hotspots/data/simulated_benchmark_hotspots.csv)",
        )
        parser.add_argument(
            "--data-type",
            type=str,
            default="simulated",
            choices=["simulated", "historical", "real"],
            help="Data nature classification: simulated, historical, or real",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing observations before importing",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate CSV and print statistics without saving to database",
        )
        parser.add_argument(
            "--no-filter-bounds",
            dest="filter_bounds",
            action="store_false",
            default=True,
            help="Do not filter out observations falling outside Nainital boundary",
        )
        parser.add_argument(
            "--filter-bounds",
            dest="filter_bounds",
            action="store_true",
            default=True,
            help="Only import observations strictly within the documented Nainital district boundary (default: True)",
        )

    def handle(self, *args, **options):
        csv_path = options["csv"]
        data_type = options["data_type"]
        clear = options["clear"]
        dry_run = options["dry_run"]
        filter_bounds = options.get("filter_bounds", True)

        if clear:
            count = Observation.objects.count()
            Observation.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {count} existing observations."))

        self.stdout.write(f"Importing observations from '{csv_path}' (type: {data_type}, dry_run: {dry_run}, filter_bounds: {filter_bounds})...")
        res = import_observations_from_csv(csv_path, default_data_nature=data_type, dry_run=dry_run, filter_bounds=filter_bounds)

        if not res["success"]:
            self.stdout.write(self.style.ERROR(f"Import failed: {res.get('error')}"))
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Validation successful: {res['valid_records']} valid rows, {res['skipped']} skipped."))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Import completed: {res['created']} created, {res['updated']} updated, {res['skipped']} skipped."
                )
            )

        if res["errors"]:
            self.stdout.write(self.style.WARNING("Validation notes / warnings:"))
            for err in res["errors"]:
                self.stdout.write(f"  - {err}")
