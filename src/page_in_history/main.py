import argparse
import logging
import pathlib

from page_in_history import quality
from page_in_history.collect import Collector, DataPaths, Slice, load_documents
from page_in_history.deck import build_deck
from page_in_history.samples import render_samples

DATA_DIR = pathlib.Path("data")
CACHE_DIR = pathlib.Path("cache")
BUILD_DIR = pathlib.Path("build")
DECK_FILE = BUILD_DIR / "page_in_history.apkg"
SAMPLES_DIR = pathlib.Path("docs/samples")
ERROR_LOG = pathlib.Path("errors.log")


def configure_logging() -> None:
    console = logging.StreamHandler()
    errors = logging.FileHandler(ERROR_LOG, mode="w")
    errors.setLevel(logging.ERROR)
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[console, errors])
    logging.getLogger("page_in_history.sources.cliopatria").setLevel(logging.ERROR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect civilizations and historical figures and build the Anki deck.")
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect", help="select civilizations and figures for a region and period")
    collect.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    collect.add_argument("--region", help="limit to a modern country, as named by Natural Earth; the whole world if left out")
    collect.add_argument("--start", type=int, default=-3400, help="first year, negative for BC")
    collect.add_argument("--end", type=int, default=2024, help="last year")

    check = commands.add_parser("check", help="report what was confirmed, left out and why")
    check.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)

    deck = commands.add_parser("deck", help="build the Anki deck from the collected data")
    deck.add_argument("--data-dir", type=pathlib.Path, default=DATA_DIR)
    deck.add_argument("--output", type=pathlib.Path, default=DECK_FILE)

    samples = commands.add_parser("samples", help="render sample cards from the built deck")
    samples.add_argument("--deck", type=pathlib.Path, default=DECK_FILE)
    samples.add_argument("--output", type=pathlib.Path, default=SAMPLES_DIR)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging()

    match args.command:
        case "collect":
            Collector(DataPaths(args.data_dir), CACHE_DIR).run(Slice(args.region, args.start, args.end))
        case "check":
            print(quality.report(DataPaths(args.data_dir)))
        case "deck":
            paths = DataPaths(args.data_dir)
            build_deck(load_documents(paths.civilizations), load_documents(paths.figures), args.data_dir, BUILD_DIR,
                       args.output)
        case "samples":
            render_samples(args.deck, args.output)


if __name__ == "__main__":
    main()
