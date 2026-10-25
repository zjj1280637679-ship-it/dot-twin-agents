"""Read one JSON request from stdin and emit one native spawn argument object."""
import argparse
import json
import sys
from .request import prepare


def unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate_key")
        out[key] = value
    return out


def bad_constant(_):
    raise ValueError("nonfinite_json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare",))
    parser.parse_args()
    try:
        request = json.loads(sys.stdin.read(), object_pairs_hook=unique, parse_constant=bad_constant)
        result = prepare(request)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        # Do not echo potentially private task text in diagnostics.
        print(json.dumps({"error": "invalid_request"}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
