from __future__ import annotations

import argparse
from typing import Any, Dict, List, Protocol

from crawlers.config import load_env, get_db_config
from crawlers.db import save_exhibitions

from crawlers.sites import (
    allMeArtSpace_DB,
    gallery_insaart_DB,
    galleryEun_DB,
    galleryMeme_DB,
    insa1010_DB,
    insaArt_DB,
    maruArtCenter_DB,
    roGallery_DB,
    seoulNoin_DB,
    sunGallery_DB,
    thePrimaArtCenter_DB,
    tongInGallery_DB,
)


class Runner(Protocol):
    def __call__(self, save_json: bool = True) -> List[Dict[str, Any]]: ...


REGISTRY: dict[str, Runner] = {
    "allMeArtSpace": allMeArtSpace_DB.run,
    "gallery_insaart": gallery_insaart_DB.run,
    "galleryEun": galleryEun_DB.run,
    "galleryMeme": galleryMeme_DB.run,
    "insa101": insa1010_DB.run,
    "insaArt": insaArt_DB.run,
    "maruArtCenter": maruArtCenter_DB.run,
    "roGallery": roGallery_DB.run,
    "seoulNoin": seoulNoin_DB.run,
    "sunGallery": sunGallery_DB.run,
    "thePrimaArtCenter": thePrimaArtCenter_DB.run,
    "tongInGallery": tongInGallery_DB.run,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only",
        nargs="*",
        help="특정 크롤러만 실행. 예: --only sunGallery roGallery",
    )
    parser.add_argument("--no-json", action="store_true", help="JSON 저장 끄기")
    return parser.parse_args()


def main() -> None:
    load_env()
    cfg = get_db_config()
    conn_info = (cfg.dbname, cfg.user, cfg.password, cfg.host, cfg.port)

    args = parse_args()
    save_json = not args.no_json

    targets = args.only if args.only else list(REGISTRY.keys())

    # 중복 제거(입력 순서 유지)
    seen = set()
    targets = [x for x in targets if not (x in seen or seen.add(x))]

    total_saved = 0
    total_fetched = 0
    failed: List[str] = []

    for name in targets:
        if name not in REGISTRY:
            print(f"[SKIP] 등록되지 않은 크롤러: {name}")
            continue

        print(f"\n========== RUN: {name} ==========")

        try:
            data = REGISTRY[name](save_json=save_json)
        except Exception as e:
            print(f"[ERROR] crawler failed: {name} -> {e}")
            failed.append(name)
            continue

        if not data:
            print(f"[INFO] {name}: fetched 0 rows (skip DB)")
            continue

        total_fetched += len(data)

        try:
            saved = save_exhibitions(conn_info, data)
        except Exception as e:
            print(f"[ERROR] DB save failed: {name} -> {e}")
            failed.append(name)
            continue

        print(f"[DB] {name}: fetched {len(data)} / inserted {saved}")
        total_saved += saved

    print("\n========== SUMMARY ==========")
    print(f"targets: {len(targets)}")
    print(f"total fetched rows = {total_fetched}")
    print(f"total inserted rows = {total_saved}")
    if failed:
        print(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
