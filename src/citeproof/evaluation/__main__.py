"""`python -m citeproof.evaluation`: chạy đánh giá trên `CITEPROOF_EVAL_DATABASE_URL` (spec §11).

    run --dataset <jsonl> --config <yaml> --mode read_write|replay|fresh [--split dev]
        [--out benchmark/results] [--dump] [--provider-identity <định danh>]
    run-refs --dataset <jsonl> --mode read_write|replay|fresh [--out benchmark/results]
        [--provider-identity <định danh>]

`replay` (spec) và `replay_only` (tên trong mã) cùng là `CacheMode.REPLAY_ONLY`: không gọi LLM,
thiếu cache thì lần chạy FAILED và lệnh thoát mã 1. `--provider-identity` (ví dụ
`openai_compatible:host:8000`) chọn cache của provider đó khi phát lại mà không cần cấu hình nó;
mặc định là định danh của provider đang cấu hình, hoặc `anthropic`. Lỗi chỉ in mã và thông điệp của
`CiteProofError` (không chứa nội dung bộ dữ liệu).
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from citeproof.core.errors import CiteProofError
from citeproof.core.logging import configure_logging
from citeproof.core.settings import Settings
from citeproof.evaluation.runner import run_eval, run_reference_eval
from citeproof.llm.types import CacheMode

DEFAULT_OUT = Path("benchmark/results")
_MODES = {
    "read_write": CacheMode.READ_WRITE,
    "replay": CacheMode.REPLAY_ONLY,
    "replay_only": CacheMode.REPLAY_ONLY,
    "fresh": CacheMode.FRESH,
}


def parse_mode(value: str) -> CacheMode:
    try:
        return _MODES[value]
    except KeyError:
        raise argparse.ArgumentTypeError(f"mode phải là một trong {', '.join(_MODES)}") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m citeproof.evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="đánh giá phán quyết claim (D1-D4)")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--mode", type=parse_mode, required=True)
    run.add_argument("--split", default=None)
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run.add_argument("--dump", action="store_true")
    run.add_argument("--provider-identity", default=None)
    refs = commands.add_parser("run-refs", help="đánh giá trạng thái tài liệu tham khảo (D5)")
    refs.add_argument("--dataset", type=Path, required=True)
    refs.add_argument("--mode", type=parse_mode, required=True)
    refs.add_argument("--out", type=Path, default=DEFAULT_OUT)
    refs.add_argument("--provider-identity", default=None)
    return parser


async def _run(args: argparse.Namespace, settings: Settings) -> Path:
    if args.command == "run":
        report = await run_eval(
            settings,
            dataset_path=args.dataset,
            config_path=args.config,
            mode=args.mode,
            split=args.split,
            out_dir=args.out,
            dump=args.dump,
            provider_identity=args.provider_identity,
        )
        print(f"macro_f1={report.metrics.macro_f1:.4f} n={report.metrics.n} run={report.run_id}")
    else:
        refs = await run_reference_eval(
            settings,
            dataset_path=args.dataset,
            mode=args.mode,
            out_dir=args.out,
            provider_identity=args.provider_identity,
        )
        print(f"accuracy={refs.accuracy:.4f} n={refs.n} run={refs.run_id}")
    return Path(args.out)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    try:
        out = asyncio.run(_run(args, settings))
    except CiteProofError as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    print(f"results in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
