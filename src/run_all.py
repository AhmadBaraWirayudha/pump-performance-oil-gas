"""
run_all.py -- run the three stages in order.

    python src/run_all.py                         # workbook from data/ or $DATA_PUMP_XLSX
    python src/run_all.py --workbook path/to.xlsx
    python src/run_all.py --only pump jet         # a subset of stages

Stages: pump (water model + validation), oil_gas (crude-oil viscosity
screening), jet (jet-nozzle sheet). A failing stage is reported and the
others still run (the jet sheet can be fine while the pump sheet is not, and
vice versa); the exit code is non-zero if any stage failed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workbook", help="path to the source .xlsx (overrides DATA_PUMP_XLSX and data/)")
    parser.add_argument("--only", nargs="+", choices=["pump", "oil_gas", "jet"],
                        help="run only these stages (default: all three)")
    args = parser.parse_args(argv)
    if args.workbook:
        os.environ["DATA_PUMP_XLSX"] = args.workbook

    # Imported after the env var is set; none of these touch the filesystem at import time.
    import jet_nozzle_extension
    import oil_gas_extension
    import pump_performance_model

    stages = {"pump": pump_performance_model.main,
              "oil_gas": oil_gas_extension.main,
              "jet": jet_nozzle_extension.main}
    chosen = args.only or list(stages)

    failed = []
    for name in chosen:
        print(f"\n{'#' * 70}\n# stage: {name}\n{'#' * 70}")
        t0 = time.time()
        try:
            stages[name]()
            print(f"[ok] {name} finished in {time.time() - t0:.1f}s")
        except Exception as exc:  # noqa: BLE001 - report and carry on with the other stages
            failed.append(name)
            print(f"[FAILED] {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc(limit=3)
    print("\n" + ("All requested stages finished." if not failed
                  else f"Stages that FAILED: {failed}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
