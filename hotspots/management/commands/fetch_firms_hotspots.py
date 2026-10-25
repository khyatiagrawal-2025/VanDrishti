import os
from django.conf import settings
from django.core.management.base import BaseCommand
from hotspots.acquisition import (
    acquire_and_ingest_firms_hotspots,
    get_firms_api_key,
    SUPPORTED_SOURCES,
    DEFAULT_SOURCE,
)
from hotspots.data_pipeline import import_observations_from_csv


class Command(BaseCommand):
    help = (
        "Fetch and ingest near-real-time satellite hotspot observations from NASA FIRMS API "
        "for Nainital Forest Division"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--key",
            type=str,
            default="",
            help="32-character NASA FIRMS MAP_KEY (defaults to FIRMS_MAP_KEY or NASA_FIRMS_API_KEY env var)",
        )
        parser.add_argument(
            "--api-key",
            type=str,
            default=None,
            help="NASA FIRMS MAP_KEY (defaults to FIRMS_MAP_KEY or NASA_FIRMS_API_KEY environment variable)",
        )
        parser.add_argument(
            "--source",
            type=str,
            default=DEFAULT_SOURCE,
            choices=SUPPORTED_SOURCES,
            help=f"Satellite sensor product source (default: {DEFAULT_SOURCE})",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=1,
            help="Number of days of data to query (1-10, default: 1)",
        )
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Acquisition end date (YYYY-MM-DD, optional)",
        )
        parser.add_argument(
            "--bbox",
            type=str,
            default="79.10,29.10,79.80,29.65",
            help="Bounding box 'min_lng,min_lat,max_lng,max_lat' (default: Nainital Division)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate schema and simulate scoring without committing writes to the database",
        )
        parser.add_argument(
            "--no-filter-bounds",
            action="store_true",
            help="Do not filter out observations falling outside the strict Nainital district boundary",
        )
        parser.add_argument(
            "--sample-fallback",
            action="store_true",
            help="If no API key is provided or the connection fails, safely import historical FIRMS sample data for offline demonstration",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=15,
            help="Request timeout in seconds (default: 15)",
        )

    def handle(self, *args, **options):
        api_key = options.get("api_key") or options.get("key") or None
        source = options.get("source") or DEFAULT_SOURCE
        days = options.get("days", 1)
        date = options.get("date")
        bbox = options.get("bbox", "79.10,29.10,79.80,29.65")
        dry_run = options.get("dry_run", False)
        filter_bounds = not options.get("no_filter_bounds", False)
        sample_fallback = options.get("sample_fallback", False)
        timeout = options.get("timeout", 15)

        resolved_key = (
            api_key
            or get_firms_api_key()
            or getattr(settings, "FIRMS_MAP_KEY", None)
            or os.environ.get("FIRMS_MAP_KEY", "")
        )

        if not resolved_key:
            self.stdout.write(
                self.style.ERROR(
                    "Error: NASA FIRMS API key is missing.\n"
                    "Please set the FIRMS_MAP_KEY or NASA_FIRMS_API_KEY environment variable,\n"
                    "or pass it via --api-key <YOUR_MAP_KEY>.\n"
                    "You can obtain a free key at: https://firms.modaps.eosdis.nasa.gov/api/"
                )
            )
            if sample_fallback:
                fallback_csv = os.path.join("hotspots", "data", "historical_firms_nainital_sample.csv")
                self.stdout.write(self.style.NOTICE(f"Executing offline demonstration fallback using '{fallback_csv}'..."))
                fb_res = import_observations_from_csv(
                    fallback_csv,
                    default_data_nature="historical",
                    dry_run=dry_run,
                    filter_bounds=filter_bounds,
                )
                if fb_res["success"]:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Offline fallback loaded: {fb_res.get('created', 0)} created, {fb_res.get('updated', 0)} updated (classified as 'historical')."
                        )
                    )
                else:
                    self.stdout.write(self.style.ERROR(f"Fallback import failed: {fb_res.get('error')}"))
            return

        self.stdout.write(
            f"Querying NASA FIRMS Area API for Nainital (source: {source}, days: {days}, dry_run: {dry_run})..."
        )

        res = acquire_and_ingest_firms_hotspots(
            api_key=resolved_key,
            source=source,
            day_range=days,
            date=date,
            area_bbox=bbox,
            timeout=timeout,
            dry_run=dry_run,
            filter_bounds=filter_bounds,
        )

        if not res["success"]:
            self.stdout.write(self.style.ERROR(f"Acquisition failed: {res.get('error')}"))
            if sample_fallback:
                fallback_csv = os.path.join("hotspots", "data", "historical_firms_nainital_sample.csv")
                self.stdout.write(self.style.NOTICE(f"Executing offline demonstration fallback using '{fallback_csv}'..."))
                fb_res = import_observations_from_csv(
                    fallback_csv,
                    default_data_nature="historical",
                    dry_run=dry_run,
                    filter_bounds=filter_bounds,
                )
                if fb_res["success"]:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Offline fallback loaded: {fb_res.get('created', 0)} created, {fb_res.get('updated', 0)} updated (classified as 'historical')."
                        )
                    )
                else:
                    self.stdout.write(self.style.ERROR(f"Fallback import failed: {fb_res.get('error')}"))
            return

        if res.get("status") == "no_anomalies":
            self.stdout.write(self.style.SUCCESS(f"FIRMS Query OK: {res.get('message')}"))
        elif dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Dry run successful: {res.get('total_records', res.get('total_rows', 0))} records fetched, "
                    f"{res.get('valid_records', 0)} valid inside boundary, {res.get('skipped', 0)} skipped."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Acquisition completed: {res.get('total_records', res.get('total_rows', 0))} fetched, {res.get('created', 0)} created, "
                    f"{res.get('updated', 0)} updated, {res.get('skipped', 0)} skipped."
                )
            )

        if res.get("errors"):
            self.stdout.write(self.style.WARNING("Validation notes / warnings:"))
            for err in res["errors"]:
                self.stdout.write(f"  - {err}")
